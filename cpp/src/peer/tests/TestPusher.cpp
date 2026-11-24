#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <exception>
#include <functional>
#include <future>
#include <initializer_list>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "identity/KnownKeys.hpp"
#include "identity/MessageSigner.hpp"
#include "identity/MessageVerifier.hpp"
#include "identity/NodeIdentity.hpp"
#include "msg/Channel.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "peer/PeerConnection.hpp"
#include "peer/PeerSession.hpp"
#include "peer/Pusher.hpp"
#include "storage/DataStored.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "storage/tests/Fixtures.hpp"
#include "types/Converter.hpp"

using http::Request;
using http::Response;
using identity::KnownKeys;
using identity::MessageSigner;
using identity::MessageVerifier;
using identity::NodeIdentity;
using msg::Channel;
using net::Address;
using net::ISocket;
using peer::PeerConnection;
using peer::PeerSession;
using peer::Pusher;
using storage::DataStored;
using storage::HashPrefix;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;

namespace {

using Session = Pusher::Session;
using Status = http::ResponseCode::Status;

// How long a test waits, at most, for another thread, so that it fails
// rather than waits forever when what it waits for does not come.
constexpr auto patience = std::chrono::seconds(10);

// The header that gives a body's length, as a line of a message's head.
constexpr std::string_view length_line = "Content-Length: ";

// What a node does once a run of requests has come, before it answers them.
using Answering = std::function<void()>;

// The next request on socket. Nothing once the connection has closed.
std::optional<Request> read_request(ISocket& socket) {
    std::string text;
    std::string line;
    std::size_t length = 0;

    while (socket.read_line(line) && !line.empty()) {
        text += line + "\r\n";

        if (0 == line.rfind(length_line, 0)) {
            length = std::stoul(line.substr(length_line.size()));
        }
    }

    if (text.empty()) {
        return std::nullopt;
    }

    std::istringstream sent(text + "\r\n" + Converter::text_of(socket.read_exact(length)));
    Request request;

    sent >> request;
    return request;
}

// The text of an answer of status, signed by signer.
std::string answer_of(const MessageSigner& signer, Status status) {
    Response answer;

    answer.set_status_code(status);
    answer.headers().set("Content-Length", "0");
    signer.sign(answer);
    return answer.to_string();
}

// A node on the loopback address that takes one connection, and answers the
// requests on it, on a thread of its own, until the connection closes. It
// answers them in runs of the lengths it is given, each once all of the run
// has come, and then each as it comes, once answering, if given, has run.
// It keeps each request it is sent.
class StandInNode {
public:
    StandInNode(const MessageSigner& signer, Status status, std::vector<std::size_t> runs = {},
                Answering answering = nullptr);
    ~StandInNode();
    StandInNode(const StandInNode&) = delete;
    StandInNode& operator=(const StandInNode&) = delete;
    StandInNode(StandInNode&&) = delete;
    StandInNode& operator=(StandInNode&&) = delete;

    [[nodiscard]] Address address() const;
    // The requests it has been sent, the first first.
    [[nodiscard]] std::vector<Request> requests() const;
    // Whether it has been sent count requests, waiting for them at most
    // patience.
    [[nodiscard]] bool is_asked(std::size_t count) const;

private:
    ISocket::Ptr m_listener;
    std::string m_answer;
    std::vector<std::size_t> m_runs;
    Answering m_answering;
    mutable std::mutex m_mutex;
    mutable std::condition_variable m_asked_more;
    std::vector<Request> m_requests; // Guarded by m_mutex
    std::thread m_thread;

    void _answer_all();
    [[nodiscard]] bool _answer_run(ISocket& connection, std::size_t length);
    void _keep(Request request);
};

StandInNode::StandInNode(const MessageSigner& signer, Status status,
                         std::vector<std::size_t> runs, Answering answering)
    : m_listener(ISocket::bind(Address("127.0.0.1", 0)))
    , m_answer(answer_of(signer, status))
    , m_runs(std::move(runs))
    , m_answering(std::move(answering)) {
    m_listener->listen(1);
    m_thread = std::thread([this] { _answer_all(); });
}

StandInNode::~StandInNode() {
    m_thread.join();
}

Address StandInNode::address() const {
    return m_listener->local_address();
}

std::vector<Request> StandInNode::requests() const {
    const std::scoped_lock lock(m_mutex);

    return m_requests;
}

bool StandInNode::is_asked(std::size_t count) const {
    std::unique_lock lock(m_mutex);

    const auto has_all = [this, count] { return m_requests.size() >= count; };

    return m_asked_more.wait_for(lock, patience, has_all);
}

void StandInNode::_answer_all() {
    try {
        const auto connection = m_listener->accept();
        std::size_t run = 0;

        connection->set_receive_timeout(patience);

        while (_answer_run(*connection, run < m_runs.size() ? m_runs.at(run) : 1)) {
            ++run;
        }
        // The node pushing may end the connection while answers are on their
        // way, as a session does once one fails its check.
        // NOLINTNEXTLINE(bugprone-empty-catch)
    } catch (const std::system_error&) {
    } catch (const std::exception& error) {
        ADD_FAILURE() << "The node failed to answer: " << error.what();
    }
}

// Reads length requests from connection, and then answers each. Whether
// they all came before the connection closed.
bool StandInNode::_answer_run(ISocket& connection, std::size_t length) {
    for (std::size_t read = 0; read < length; ++read) {
        auto request = read_request(connection);

        if (!request) {
            return false;
        }

        _keep(std::move(*request));
    }

    if (m_answering) {
        m_answering();
    }

    for (std::size_t answered = 0; answered < length; ++answered) {
        connection.send_all(m_answer);
    }

    return true;
}

void StandInNode::_keep(Request request) {
    {
        const std::scoped_lock lock(m_mutex);

        m_requests.push_back(std::move(request));
    }

    m_asked_more.notify_all();
}

// Waits for released to be set.
Answering waiting_for(std::shared_future<void> released) {
    return [released = std::move(released)] {
        EXPECT_EQ(released.wait_for(patience), std::future_status::ready);
    };
}

// The sessions a test has connected.
class Connected {
public:
    explicit Connected(std::vector<Session> sessions = {});

    [[nodiscard]] Pusher::GetPeers get_peers();

private:
    std::mutex m_mutex;
    std::vector<Session> m_sessions; // Guarded by m_mutex
};

Connected::Connected(std::vector<Session> sessions)
    : m_sessions(std::move(sessions)) {}

Pusher::GetPeers Connected::get_peers() {
    return [this] {
        const std::scoped_lock lock(m_mutex);

        return m_sessions;
    };
}

// The new content identifier names, as it is posted: from node, if given,
// or from a client that is no node.
DataStored stored(const Identifier& identifier,
                  const std::optional<Identifier>& node = std::nullopt) {
    return {identifier, node, 0};
}

// Whether request puts identifier's content, body, as it is stored.
bool puts(const Request& request, const Identifier& identifier, const std::string& body) {
    return request.method() == "PUT" && request.uri() == "/data/" + identifier.to_string()
           && request.headers().get("Content-Type") == "application/octet-stream"
           && request.body() == body;
}

// Each test has this node and three peers, each with a key of its own, a
// store that holds their public keys, and a store of what this node holds,
// which holds nothing to start with.
class PusherTest : public testing::Test {
protected:
    PusherTest()
        : m_keys(std::make_shared<fixtures::MemorySource>())
        , m_store(std::make_shared<fixtures::MemorySource>())
        , m_this_node(m_directory.path() / "this-node.pem", *m_keys)
        , m_first(m_directory.path() / "first.pem", *m_keys)
        , m_second(m_directory.path() / "second.pem", *m_keys)
        , m_third(m_directory.path() / "third.pem", *m_keys) {}

    // A session with peer, at node's address.
    [[nodiscard]] Session session_with(const StandInNode& node, const NodeIdentity& peer) const {
        return std::make_shared<PeerSession>(
            std::make_unique<PeerConnection>(node.address(), PeerConnection::Limits{}), peer.id(),
            MessageSigner(m_this_node), MessageVerifier(std::make_shared<KnownKeys>(m_keys), {}));
    }

    // A pusher to the peers connected gives, of what store() holds.
    [[nodiscard]] Pusher pusher_to(Connected& connected,
                                   std::size_t run_length = Pusher::default_run_length) const {
        return {m_store, connected.get_peers(), run_length};
    }

    // New content, now held, whose hash shares more leading bits with
    // peer's node id than with any other peer's, made from the next of
    // "Content 0", "Content 1", and so on, that is.
    [[nodiscard]] Identifier content_for(const NodeIdentity& peer) {
        constexpr std::size_t most_tries = 1'000;

        for (std::size_t tries = 0; tries < most_tries; ++tries) {
            const auto body = Converter::bytes_of("Content " + std::to_string(m_made++));
            const auto identifier = Identifier::from(body);

            if (_is_best_for(peer, identifier)) {
                m_store->put(identifier, body);
                return identifier;
            }
        }

        throw std::logic_error("No content matches the peer best");
    }

    // count pieces of new content, as content_for() makes each, in the
    // order made.
    [[nodiscard]] std::vector<Identifier> contents_for(const NodeIdentity& peer,
                                                       std::size_t count) {
        std::vector<Identifier> contents;

        while (contents.size() < count) {
            contents.push_back(content_for(peer));
        }

        return contents;
    }

    // The text of what store() holds under identifier, or nothing.
    [[nodiscard]] std::string held(const Identifier& identifier) const {
        const auto body = m_store->get(identifier);

        return body ? Converter::text_of(*body) : std::string();
    }

    // Lets go of identifier's content, as eviction would.
    void let_go(const Identifier& identifier) const {
        (void)m_store->remove(identifier);
    }

    // Holds this node's public key as content, as its id names it.
    void hold_own_key() const {
        const auto key = m_keys->get(own_id());

        if (key) {
            m_store->put(own_id(), *key);
        }
    }

    [[nodiscard]] const Identifier& own_id() const {
        return m_this_node.id();
    }

    [[nodiscard]] const NodeIdentity& first() const {
        return m_first;
    }

    [[nodiscard]] const NodeIdentity& second() const {
        return m_second;
    }

    [[nodiscard]] const NodeIdentity& third() const {
        return m_third;
    }

private:
    fixtures::ScratchDirectory m_directory;
    std::shared_ptr<fixtures::MemorySource> m_keys;
    std::shared_ptr<fixtures::MemorySource> m_store;
    NodeIdentity m_this_node;
    NodeIdentity m_first;
    NodeIdentity m_second;
    NodeIdentity m_third;
    std::size_t m_made{0};

    // Whether identifier's hash shares more leading bits with peer's node id
    // than with any other peer's.
    [[nodiscard]] bool _is_best_for(const NodeIdentity& peer, const Identifier& identifier) const {
        const auto bits = [&identifier](const NodeIdentity& node) {
            return HashPrefix::parse(node.id().hash()).matching_bits(identifier);
        };
        const auto best = bits(peer);
        const auto peers = {&m_first, &m_second, &m_third};

        return std::none_of(peers.begin(), peers.end(), [&](const NodeIdentity* other) {
            return other != &peer && bits(*other) >= best;
        });
    }
};

// Whether making a pusher of store, get_peers, and run_length is refused.
bool refused(const IDataSource::Ptr& store, const Pusher::GetPeers& get_peers,
             std::size_t run_length) {
    try {
        const Pusher pusher(store, get_peers, run_length);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

} // namespace

TEST_F(PusherTest, ContentGoesToTheBestMatch) {
    const StandInNode first_node(MessageSigner(first()), Status::Created);
    const StandInNode second_node(MessageSigner(second()), Status::Created);
    const StandInNode third_node(MessageSigner(third()), Status::Created);
    Connected connected({
        session_with(first_node, first()),
        session_with(second_node, second()),
        session_with(third_node, third()),
    });
    const auto content = content_for(second());
    const auto pusher = pusher_to(connected);

    EXPECT_EQ(pusher.push({stored(content)}), 1U);

    const auto requests = second_node.requests();

    ASSERT_EQ(requests.size(), 1U);
    EXPECT_TRUE(puts(requests.at(0), content, held(content)));
    EXPECT_TRUE(first_node.requests().empty());
    EXPECT_TRUE(third_node.requests().empty());
}

TEST_F(PusherTest, ContentForEachPeerGoesToEach) {
    const StandInNode first_node(MessageSigner(first()), Status::Created);
    const StandInNode second_node(MessageSigner(second()), Status::Created);
    const StandInNode third_node(MessageSigner(third()), Status::Created);
    Connected connected({
        session_with(first_node, first()),
        session_with(second_node, second()),
        session_with(third_node, third()),
    });
    const auto for_first = content_for(first());
    const auto for_third = content_for(third());
    const auto pusher = pusher_to(connected);

    EXPECT_EQ(pusher.push({stored(for_first), stored(for_third)}), 2U);

    const auto first_requests = first_node.requests();
    const auto third_requests = third_node.requests();

    ASSERT_EQ(first_requests.size(), 1U);
    EXPECT_TRUE(puts(first_requests.at(0), for_first, held(for_first)));
    ASSERT_EQ(third_requests.size(), 1U);
    EXPECT_TRUE(puts(third_requests.at(0), for_third, held(for_third)));
    EXPECT_TRUE(second_node.requests().empty());
}

// The node answers only once all three have come, which they would not if
// each waited for the answer to the one before.
TEST_F(PusherTest, ContentBoundForOnePeerGoesInOneRun) {
    const StandInNode first_node(MessageSigner(first()), Status::Created, {3});
    Connected connected({session_with(first_node, first())});
    const auto contents = contents_for(first(), 3);
    const auto pusher = pusher_to(connected);

    EXPECT_EQ(pusher.push({stored(contents.at(0)), stored(contents.at(1)), stored(contents.at(2))}),
              3U);

    const auto requests = first_node.requests();

    ASSERT_EQ(requests.size(), 3U);

    for (std::size_t index = 0; index < contents.size(); ++index) {
        EXPECT_TRUE(puts(requests.at(index), contents.at(index), held(contents.at(index))));
    }
}

TEST_F(PusherTest, ContentBeyondARunGoesInTheNext) {
    constexpr std::size_t run_length = 2;
    const StandInNode first_node(MessageSigner(first()), Status::Created, {run_length});
    Connected connected({session_with(first_node, first())});
    const auto contents = contents_for(first(), 3);
    const auto pusher = pusher_to(connected, run_length);

    EXPECT_EQ(pusher.push({stored(contents.at(0)), stored(contents.at(1)), stored(contents.at(2))}),
              3U);

    const auto requests = first_node.requests();

    ASSERT_EQ(requests.size(), 3U);
    EXPECT_TRUE(puts(requests.at(2), contents.at(2), held(contents.at(2))));
}

TEST_F(PusherTest, NotPushedBackToWhereItCameFrom) {
    const StandInNode first_node(MessageSigner(first()), Status::Created);
    const StandInNode second_node(MessageSigner(second()), Status::Created);
    Connected connected({session_with(first_node, first()), session_with(second_node, second())});
    const auto content = content_for(first());
    const auto pusher = pusher_to(connected);

    EXPECT_EQ(pusher.push({stored(content, first().id())}), 0U);
    EXPECT_TRUE(first_node.requests().empty());
    EXPECT_TRUE(second_node.requests().empty());
}

TEST_F(PusherTest, PushedFromAnotherNode) {
    const StandInNode first_node(MessageSigner(first()), Status::Created);
    const StandInNode second_node(MessageSigner(second()), Status::Created);
    Connected connected({session_with(first_node, first()), session_with(second_node, second())});
    const auto content = content_for(first());
    const auto pusher = pusher_to(connected);

    EXPECT_EQ(pusher.push({stored(content, second().id())}), 1U);
    EXPECT_EQ(first_node.requests().size(), 1U);
    EXPECT_TRUE(second_node.requests().empty());
}

// This node's own key matches its id in every bit, as no peer's can.
TEST_F(PusherTest, PushedEvenWhereThisNodeMatchesBetter) {
    const StandInNode first_node(MessageSigner(first()), Status::Created);
    Connected connected({session_with(first_node, first())});
    const auto pusher = pusher_to(connected);

    hold_own_key();

    EXPECT_EQ(pusher.push({stored(own_id(), own_id())}), 1U);

    const auto requests = first_node.requests();

    ASSERT_EQ(requests.size(), 1U);
    EXPECT_TRUE(puts(requests.at(0), own_id(), held(own_id())));
}

TEST_F(PusherTest, ContentNoLongerHeldIsNotPushed) {
    const StandInNode first_node(MessageSigner(first()), Status::Created);
    Connected connected({session_with(first_node, first())});
    const auto content = content_for(first());
    const auto pusher = pusher_to(connected);

    let_go(content);

    EXPECT_EQ(pusher.push({stored(content)}), 0U);
    EXPECT_TRUE(first_node.requests().empty());
}

TEST_F(PusherTest, ClosedPeerIsPassedOver) {
    const StandInNode first_node(MessageSigner(first()), Status::Created);
    const StandInNode second_node(MessageSigner(second()), Status::Created);
    const auto closed = session_with(first_node, first());
    Connected connected({closed, session_with(second_node, second())});
    const auto content = content_for(first());
    const auto pusher = pusher_to(connected);

    closed->close();

    EXPECT_EQ(pusher.push({stored(content)}), 1U);

    const auto requests = second_node.requests();

    ASSERT_EQ(requests.size(), 1U);
    EXPECT_TRUE(puts(requests.at(0), content, held(content)));
    EXPECT_TRUE(first_node.requests().empty());
}

TEST_F(PusherTest, NoPeerToPushTo) {
    Connected connected;
    const auto content = content_for(first());
    const auto pusher = pusher_to(connected);

    EXPECT_EQ(pusher.push({stored(content)}), 0U);
}

TEST_F(PusherTest, RefusedContentIsNotCounted) {
    const StandInNode first_node(MessageSigner(first()), Status::InsufficientStorage);
    Connected connected({session_with(first_node, first())});
    const auto content = content_for(first());
    const auto pusher = pusher_to(connected);

    EXPECT_EQ(pusher.push({stored(content)}), 0U);
    EXPECT_EQ(first_node.requests().size(), 1U);
}

// The answer is signed by another node, which ends the session.
TEST_F(PusherTest, FailedRunEndsThePushesToThatPeer) {
    const StandInNode first_node(MessageSigner(second()), Status::Created);
    Connected connected({session_with(first_node, first())});
    const auto one = content_for(first());
    const auto another = content_for(first());
    const auto pusher = pusher_to(connected, 1);

    EXPECT_EQ(pusher.push({stored(one), stored(another)}), 0U);
    EXPECT_EQ(first_node.requests().size(), 1U);
}

TEST_F(PusherTest, NewContentPostedIsPushed) {
    const StandInNode first_node(MessageSigner(first()), Status::Created);
    Connected connected({session_with(first_node, first())});
    const auto content = content_for(first());
    const auto pusher = pusher_to(connected);

    Channel::post(DataStored::name, stored(content));

    ASSERT_TRUE(first_node.is_asked(1));
    EXPECT_TRUE(puts(first_node.requests().at(0), content, held(content)));
}

// The first is answered once the others are stored, and the node answers
// the others only once both have come.
TEST_F(PusherTest, ContentStoredWhilePushingGoesInOneRun) {
    std::promise<void> release;
    const StandInNode first_node(MessageSigner(first()), Status::Created, {1, 2},
                                 waiting_for(release.get_future().share()));
    Connected connected({session_with(first_node, first())});
    const auto contents = contents_for(first(), 3);
    const auto pusher = pusher_to(connected);

    Channel::post(DataStored::name, stored(contents.at(0)));
    ASSERT_TRUE(first_node.is_asked(1));
    Channel::post(DataStored::name, stored(contents.at(1)));
    Channel::post(DataStored::name, stored(contents.at(2)));
    release.set_value();

    ASSERT_TRUE(first_node.is_asked(3));

    const auto requests = first_node.requests();

    EXPECT_TRUE(puts(requests.at(1), contents.at(1), held(contents.at(1))));
    EXPECT_TRUE(puts(requests.at(2), contents.at(2), held(contents.at(2))));
}

TEST_F(PusherTest, MissingPartsAreRefused) {
    const auto store = std::make_shared<fixtures::MemorySource>();
    Connected connected;

    EXPECT_TRUE(refused(nullptr, connected.get_peers(), Pusher::default_run_length));
    EXPECT_TRUE(refused(store, nullptr, Pusher::default_run_length));
    EXPECT_TRUE(refused(store, connected.get_peers(), 0));
    EXPECT_FALSE(refused(store, connected.get_peers(), 1));
}
