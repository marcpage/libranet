#include <gtest/gtest.h>
#include <chrono>
#include <cstddef>
#include <exception>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "identity/KnownKeys.hpp"
#include "identity/MessageSigner.hpp"
#include "identity/MessageVerifier.hpp"
#include "identity/NodeIdentity.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "peer/HandOff.hpp"
#include "peer/PeerConnection.hpp"
#include "peer/PeerSession.hpp"
#include "storage/Identifier.hpp"
#include "storage/Zlib.hpp"
#include "storage/tests/Fixtures.hpp"
#include "types/Converter.hpp"

using http::Response;
using identity::KnownKeys;
using identity::MessageSigner;
using identity::MessageVerifier;
using identity::NodeIdentity;
using net::Address;
using net::ISocket;
using peer::HandOff;
using peer::PeerConnection;
using peer::PeerSession;
using storage::Identifier;
using storage::Zlib;
using types::Converter;

namespace {

using Session = HandOff::Session;
using Status = http::ResponseCode::Status;

// How long a test waits, at most, for another thread, so that it fails
// rather than waits forever when what it waits for does not come.
constexpr auto patience = std::chrono::seconds(10);

// The header that gives a body's length, as a line of a message's head.
constexpr std::string_view length_line = "Content-Length: ";

// What this node holds, and means to let go of.
constexpr std::string_view content_text = "Content this node lets go of";

// A request as a node was sent it: its request line, and its body.
struct Sent {
    std::string line;
    std::string body;
};

// The next request on socket, as sent. Nothing once the connection has
// closed.
std::optional<Sent> read_request(ISocket& socket) {
    std::string line;
    std::string request_line;
    std::size_t length = 0;

    while (socket.read_line(line) && !line.empty()) {
        if (request_line.empty()) {
            request_line = line;
        }

        if (0 == line.rfind(length_line, 0)) {
            length = std::stoul(line.substr(length_line.size()));
        }
    }

    if (request_line.empty()) {
        return std::nullopt;
    }

    return Sent{request_line, Converter::text_of(socket.read_exact(length))};
}

// A node on the loopback address that takes one connection, and answers each
// request on it in turn, on a thread of its own, until the connection
// closes. It keeps each request it was sent.
class StandInNode {
public:
    // Answers each request with answer, the text of a response.
    explicit StandInNode(std::string answer);
    ~StandInNode();
    StandInNode(const StandInNode&) = delete;
    StandInNode& operator=(const StandInNode&) = delete;
    StandInNode(StandInNode&&) = delete;
    StandInNode& operator=(StandInNode&&) = delete;

    [[nodiscard]] Address address() const;
    // Each request it has been sent, the first first.
    [[nodiscard]] std::vector<Sent> requests() const;
    [[nodiscard]] std::size_t asked() const;

private:
    ISocket::Ptr m_listener;
    std::string m_answer;
    mutable std::mutex m_mutex;
    std::vector<Sent> m_requests; // Guarded by m_mutex
    std::thread m_thread;

    void _answer_all();
    void _keep(Sent request);
};

StandInNode::StandInNode(std::string answer)
    : m_listener(ISocket::bind(Address("127.0.0.1", 0)))
    , m_answer(std::move(answer)) {
    m_listener->listen(1);
    m_thread = std::thread([this] { _answer_all(); });
}

StandInNode::~StandInNode() {
    m_thread.join();
}

Address StandInNode::address() const {
    return m_listener->local_address();
}

std::vector<Sent> StandInNode::requests() const {
    const std::scoped_lock lock(m_mutex);

    return m_requests;
}

std::size_t StandInNode::asked() const {
    return requests().size();
}

void StandInNode::_answer_all() {
    try {
        const auto connection = m_listener->accept();

        connection->set_receive_timeout(patience);

        while (auto request = read_request(*connection)) {
            _keep(std::move(*request));
            connection->send_all(m_answer);
        }
        // The node offering may end the connection while answers are on
        // their way, as a session does once one fails its check.
        // NOLINTNEXTLINE(bugprone-empty-catch)
    } catch (const std::system_error&) {
    } catch (const std::exception& error) {
        ADD_FAILURE() << "The node failed to answer: " << error.what();
    }
}

void StandInNode::_keep(Sent request) {
    const std::scoped_lock lock(m_mutex);

    m_requests.push_back(std::move(request));
}

// The text of an answer of status, with no body, signed by signer.
std::string answer(const MessageSigner& signer, Status status) {
    Response made;

    made.set_status_code(status);
    made.headers().set("Content-Length", "0");
    signer.sign(made);
    return made.to_string();
}

// The answer of a node that takes what it is offered.
std::string taking(const MessageSigner& signer) {
    return answer(signer, Status::Created);
}

// The answer of a node that held what it is offered already.
std::string holding(const MessageSigner& signer) {
    return answer(signer, Status::Ok);
}

// The answer of a node that has no room for what it is offered.
std::string full(const MessageSigner& signer) {
    return answer(signer, Status::InsufficientStorage);
}

// The request line of a PUT of identifier's content.
std::string put_of(const Identifier& identifier) {
    return "PUT /data/" + identifier.to_string() + " HTTP/1.1";
}

// The peers sessions names, as they are when the hand-off asks.
HandOff::GetPeers connected(std::vector<Session> sessions) {
    return [sessions = std::move(sessions)] { return sessions; };
}

// Each test has this node and two peers, each with a key of its own, a
// store that holds their public keys, and a store of what this node holds,
// which holds the content it lets go of.
class HandOffTest : public testing::Test {
protected:
    HandOffTest()
        : m_keys(std::make_shared<fixtures::MemorySource>())
        , m_store(std::make_shared<fixtures::MemorySource>())
        , m_this_node(m_directory.path() / "this-node.pem", *m_keys)
        , m_first(m_directory.path() / "first.pem", *m_keys)
        , m_second(m_directory.path() / "second.pem", *m_keys) {
        m_store->put(content_id(), Converter::bytes_of(content_text));
    }

    // A session with peer, at node's address.
    [[nodiscard]] Session session_with(const StandInNode& node, const NodeIdentity& peer) const {
        return std::make_shared<PeerSession>(
            std::make_unique<PeerConnection>(node.address(), PeerConnection::Limits{}), peer.id(),
            MessageSigner(m_this_node), MessageVerifier(std::make_shared<KnownKeys>(m_keys), {}));
    }

    // A hand-off of what store() holds to sessions.
    [[nodiscard]] HandOff hand_off_to(std::vector<Session> sessions) const {
        return {m_store, connected(std::move(sessions))};
    }

    // Puts peer's public key in store(), as the content its id names, whose
    // hash that peer's id matches best of all, and gives its identifier.
    [[nodiscard]] Identifier hold_key_of(const NodeIdentity& peer) const {
        const auto key = m_keys->get(peer.id());

        EXPECT_TRUE(key);

        if (key) {
            m_store->put(peer.id(), *key);
        }

        return peer.id();
    }

    // The identifier of content_text.
    [[nodiscard]] static Identifier content_id() {
        return Identifier::from(Converter::bytes_of(content_text));
    }

    [[nodiscard]] const std::shared_ptr<fixtures::MemorySource>& store() const {
        return m_store;
    }

    [[nodiscard]] const NodeIdentity& first() const {
        return m_first;
    }

    [[nodiscard]] const NodeIdentity& second() const {
        return m_second;
    }

private:
    fixtures::ScratchDirectory m_directory;
    std::shared_ptr<fixtures::MemorySource> m_keys;
    std::shared_ptr<fixtures::MemorySource> m_store;
    NodeIdentity m_this_node;
    NodeIdentity m_first;
    NodeIdentity m_second;
};

} // namespace

// NOLINTBEGIN(bugprone-unchecked-optional-access)

TEST_F(HandOffTest, PeerThatTakesItIsNamed) {
    const StandInNode peer(taking(MessageSigner(first())));
    const auto hand_off = hand_off_to({session_with(peer, first())});
    const auto taker = hand_off.hand_off(content_id());

    ASSERT_TRUE(taker);
    EXPECT_EQ(taker->to_string(), first().id().to_string());
    ASSERT_EQ(peer.asked(), 1U);
    EXPECT_EQ(peer.requests().at(0).line, put_of(content_id()));
    EXPECT_EQ(peer.requests().at(0).body, content_text);
    EXPECT_TRUE(store()->has(content_id()));
}

// A peer that answers it holds the content already has taken it.
TEST_F(HandOffTest, PeerThatHoldsItTakesIt) {
    const StandInNode peer(holding(MessageSigner(first())));
    const auto hand_off = hand_off_to({session_with(peer, first())});
    const auto taker = hand_off.hand_off(content_id());

    ASSERT_TRUE(taker);
    EXPECT_EQ(taker->to_string(), first().id().to_string());
}

// Content kept compressed is offered as it is kept, as a peer checks either
// (HttpApi §7.1).
TEST_F(HandOffTest, ContentIsOfferedAsStored) {
    const auto compressed = Converter::text_of(
        Zlib::compress(Converter::bytes_of(content_text), Zlib::balance_level));
    const StandInNode peer(taking(MessageSigner(first())));
    const auto hand_off = hand_off_to({session_with(peer, first())});

    store()->put(content_id(), Converter::bytes_of(compressed));

    EXPECT_TRUE(hand_off.hand_off(content_id()));
    ASSERT_EQ(peer.asked(), 1U);
    EXPECT_EQ(peer.requests().at(0).body, compressed);
}

// The content offered is the second peer's key, which its id names, so its
// id matches best, though the first peer is connected first.
TEST_F(HandOffTest, BestMatchIsOfferedItFirst) {
    const auto offered = hold_key_of(second());
    const StandInNode first_peer(taking(MessageSigner(first())));
    const StandInNode second_peer(taking(MessageSigner(second())));
    const auto hand_off =
        hand_off_to({session_with(first_peer, first()), session_with(second_peer, second())});
    const auto taker = hand_off.hand_off(offered);

    ASSERT_TRUE(taker);
    EXPECT_EQ(taker->to_string(), second().id().to_string());
    EXPECT_EQ(first_peer.asked(), 0U);
    ASSERT_EQ(second_peer.asked(), 1U);
    EXPECT_EQ(second_peer.requests().at(0).line, put_of(offered));
}

TEST_F(HandOffTest, PeerThatDoesNotTakeItIsPassedOver) {
    const auto offered = hold_key_of(second());
    const StandInNode first_peer(taking(MessageSigner(first())));
    const StandInNode second_peer(full(MessageSigner(second())));
    const auto hand_off =
        hand_off_to({session_with(first_peer, first()), session_with(second_peer, second())});
    const auto taker = hand_off.hand_off(offered);

    ASSERT_TRUE(taker);
    EXPECT_EQ(taker->to_string(), first().id().to_string());
    EXPECT_EQ(second_peer.asked(), 1U);
    ASSERT_EQ(first_peer.asked(), 1U);
    EXPECT_EQ(first_peer.requests().at(0).line, put_of(offered));
}

// The second peer, whose id the content offered names, is offered it first,
// and its unsigned answer ends its session.
TEST_F(HandOffTest, PeerThatCannotBeAskedIsPassedOver) {
    const auto offered = hold_key_of(second());
    const StandInNode first_peer(taking(MessageSigner(first())));
    const StandInNode second_peer("HTTP/1.1 201 Created\r\nContent-Length: 0\r\n\r\n");
    const auto second_session = session_with(second_peer, second());
    const auto hand_off = hand_off_to({session_with(first_peer, first()), second_session});
    const auto taker = hand_off.hand_off(offered);

    ASSERT_TRUE(taker);
    EXPECT_EQ(taker->to_string(), first().id().to_string());
    EXPECT_EQ(second_peer.asked(), 1U);
    EXPECT_FALSE(second_session->is_open());
    EXPECT_EQ(first_peer.asked(), 1U);
}

TEST_F(HandOffTest, ClosedSessionIsNotOffered) {
    const StandInNode first_peer(taking(MessageSigner(first())));
    const StandInNode second_peer(taking(MessageSigner(second())));
    const auto first_session = session_with(first_peer, first());
    const auto hand_off = hand_off_to({first_session, nullptr, session_with(second_peer, second())});

    first_session->close();

    const auto taker = hand_off.hand_off(content_id());

    ASSERT_TRUE(taker);
    EXPECT_EQ(taker->to_string(), second().id().to_string());
    EXPECT_EQ(first_peer.asked(), 0U);
    EXPECT_EQ(second_peer.asked(), 1U);
}

// NOLINTEND(bugprone-unchecked-optional-access)

TEST_F(HandOffTest, NoneTakingItIsSaid) {
    const StandInNode first_peer(full(MessageSigner(first())));
    const StandInNode second_peer(full(MessageSigner(second())));
    const auto hand_off =
        hand_off_to({session_with(first_peer, first()), session_with(second_peer, second())});

    EXPECT_FALSE(hand_off.hand_off(content_id()));
    EXPECT_EQ(first_peer.asked(), 1U);
    EXPECT_EQ(second_peer.asked(), 1U);
    EXPECT_TRUE(store()->has(content_id()));
}

TEST_F(HandOffTest, NoPeerIsNoneTakingIt) {
    const auto hand_off = hand_off_to({});

    EXPECT_FALSE(hand_off.hand_off(content_id()));
}

TEST_F(HandOffTest, ContentNotHeldIsNotOffered) {
    const StandInNode peer(taking(MessageSigner(first())));
    const auto hand_off = hand_off_to({session_with(peer, first())});

    EXPECT_FALSE(hand_off.hand_off(Identifier::from(Converter::bytes_of("Not held"))));
    EXPECT_EQ(peer.asked(), 0U);
}

TEST_F(HandOffTest, NeedsAStoreAndPeers) {
    EXPECT_THROW((void)HandOff(nullptr, connected({})), std::invalid_argument);
    EXPECT_THROW((void)HandOff(store(), nullptr), std::invalid_argument);
}
