#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <cstddef>
#include <exception>
#include <iterator>
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
#include "logger/Logger.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "peer/NodeListPublisher.hpp"
#include "peer/PeerConnection.hpp"
#include "peer/PeerSession.hpp"
#include "protocol/NodeList.hpp"
#include "storage/Identifier.hpp"
#include "storage/tests/Fixtures.hpp"
#include "types/Converter.hpp"

using http::Request;
using http::Response;
using http::ResponseCode;
using identity::KnownKeys;
using identity::MessageSigner;
using identity::MessageVerifier;
using identity::NodeIdentity;
using logger::Logger;
using net::Address;
using net::ISocket;
using peer::NodeListPublisher;
using peer::PeerConnection;
using peer::PeerSession;
using protocol::NodeList;
using storage::Identifier;
using types::Converter;

namespace {

// How long a node waits, at most, for this node to send something, so that
// a test fails rather than waits forever when it does not come.
constexpr auto patience = std::chrono::seconds(10);

// The header that gives a body's length, as a line of a message's head.
constexpr std::string_view length_line = "Content-Length: ";

// This node's own endpoint, and those of other nodes.
constexpr auto own_endpoint = "http://localhost:8080";
constexpr auto first_endpoint = "http://192.0.2.1:8080";
constexpr auto second_endpoint = "http://192.0.2.2:8080";
constexpr auto third_endpoint = "http://192.0.2.3:8080";

// The next request on socket: its line and headers, each ended with a CRLF,
// and its body, as long as its Content-Length gives. Nothing once the
// connection has closed.
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

// An answer with the status, signed by signer unless there is none.
Response answer_of(ResponseCode::Status status, const MessageSigner* signer) {
    Response answer;

    answer.set_status_code(status);
    answer.headers().set("Content-Length", "0");

    if (nullptr != signer) {
        signer->sign(answer);
    }

    return answer;
}

// A node on the loopback address that takes one connection, and answers
// each request on it in turn, on a thread of its own, with an answer of
// status, signed by signer unless there is none, until the connection
// closes. It keeps each request it is sent.
class StandInNode {
public:
    StandInNode(ResponseCode::Status status, std::optional<MessageSigner> signer);
    ~StandInNode();
    StandInNode(const StandInNode&) = delete;
    StandInNode& operator=(const StandInNode&) = delete;
    StandInNode(StandInNode&&) = delete;
    StandInNode& operator=(StandInNode&&) = delete;

    [[nodiscard]] Address address() const;
    // The requests it has been sent, in order.
    [[nodiscard]] std::vector<Request> requests() const;

private:
    ISocket::Ptr m_listener;
    mutable std::mutex m_guard;
    std::vector<Request> m_requests;
    std::thread m_thread;
};

StandInNode::StandInNode(ResponseCode::Status status, std::optional<MessageSigner> signer)
    : m_listener(ISocket::bind(Address("127.0.0.1", 0))) {
    m_listener->listen(1);
    m_thread = std::thread([this, status, signer = std::move(signer)] {
        try {
            const auto connection = m_listener->accept();

            connection->set_receive_timeout(patience);

            while (auto request = read_request(*connection)) {
                {
                    const std::scoped_lock lock(m_guard);

                    m_requests.push_back(std::move(*request));
                }

                connection->send_all(answer_of(status, signer ? &*signer : nullptr).to_string());
            }
            // This node may end the connection while answers are on their way,
            // as a session does once one fails its check.
            // NOLINTNEXTLINE(bugprone-empty-catch)
        } catch (const std::system_error&) {
        } catch (const std::exception& error) {
            ADD_FAILURE() << "The node failed to answer: " << error.what();
        }
    });
}

StandInNode::~StandInNode() {
    m_thread.join();
}

Address StandInNode::address() const {
    return m_listener->local_address();
}

std::vector<Request> StandInNode::requests() const {
    const std::scoped_lock lock(m_guard);

    return m_requests;
}

// The endpoint and node id of each entry of list, as text, in order.
std::vector<std::string> named_in(const NodeList& list) {
    std::vector<std::string> named;

    std::transform(list.entries().begin(), list.entries().end(), std::back_inserter(named),
                   [](const NodeList::Entry& entry) {
        return entry.endpoint + " " + entry.node_id.to_string();
    });
    return named;
}

// The entry of endpoint naming node_id, as named_in() gives it.
std::string entry(std::string_view endpoint, const Identifier& node_id) {
    return std::string(endpoint) + " " + node_id.to_string();
}

// The id of a node no test runs, named by text.
Identifier node_named(std::string_view text) {
    return Identifier::from(Converter::bytes_of(text));
}

// Each test has this node, two peers it may have sessions with, and another
// node, each with a key of its own, and a store that holds their public
// keys. It keeps what is logged.
class NodeListPublisherTest : public testing::Test {
protected:
    NodeListPublisherTest()
        : m_store(std::make_shared<fixtures::MemorySource>())
        , m_this_node(m_directory.path() / "this-node.pem", *m_store)
        , m_first(m_directory.path() / "first.pem", *m_store)
        , m_second(m_directory.path() / "second.pem", *m_store)
        , m_lines(std::make_shared<std::vector<std::string>>()) {}

    void SetUp() override {
        Logger::remove_all_outputs();
        Logger::add_output(std::make_unique<fixtures::KeepingOutput>(m_lines));
        Logger::reset_levels(Logger::Level::Info);
    }

    void TearDown() override {
        Logger::remove_all_outputs();
    }

    [[nodiscard]] const Identifier& own_id() const {
        return m_this_node.id();
    }

    [[nodiscard]] const Identifier& first_id() const {
        return m_first.id();
    }

    [[nodiscard]] const Identifier& second_id() const {
        return m_second.id();
    }

    [[nodiscard]] MessageSigner first_signer() const {
        return MessageSigner(m_first);
    }

    [[nodiscard]] MessageSigner second_signer() const {
        return MessageSigner(m_second);
    }

    // A publisher for this node, at its own endpoint.
    [[nodiscard]] NodeListPublisher publisher() const {
        return {own_id(), {own_endpoint}};
    }

    // A session with node_id, at address.
    [[nodiscard]] std::shared_ptr<PeerSession> session_with(const Identifier& node_id,
                                                            const Address& address) const {
        return std::make_shared<PeerSession>(
            std::make_unique<PeerConnection>(address, PeerConnection::Limits{}), node_id,
            MessageSigner(m_this_node), MessageVerifier(std::make_shared<KnownKeys>(m_store), {}));
    }

    // Whether request was signed by this node, with its key.
    [[nodiscard]] bool signed_by_this_node(const Request& request) const {
        const auto signer = MessageVerifier(std::make_shared<KnownKeys>(m_store), {}).verify(request);

        return signer && signer->verified
               && signer->node_id.to_string() == m_this_node.id().to_string();
    }

    // The entries this node's own endpoint names first, then each of others.
    [[nodiscard]] std::vector<std::string> own_then(const std::vector<std::string>& others) const {
        std::vector<std::string> named{entry(own_endpoint, own_id())};

        named.insert(named.end(), others.begin(), others.end());
        return named;
    }

    // The JSON of a list naming this node's own endpoint, then others, in
    // order, as a list is posted.
    [[nodiscard]] std::string list_of(const NodeList::Entries& others) const {
        NodeList list(own_id(), {own_endpoint});

        for (const auto& other : others) {
            list.add(other.endpoint, other.node_id);
        }

        return list.to_string();
    }

    // Whether a line was logged that holds text.
    [[nodiscard]] bool logged(std::string_view text) const {
        return std::any_of(m_lines->begin(), m_lines->end(), [text](const std::string& line) {
            return std::string::npos != line.find(text);
        });
    }

private:
    fixtures::ScratchDirectory m_directory;
    std::shared_ptr<fixtures::MemorySource> m_store;
    NodeIdentity m_this_node;
    NodeIdentity m_first;
    NodeIdentity m_second;
    std::shared_ptr<std::vector<std::string>> m_lines;
};

} // namespace

TEST_F(NodeListPublisherTest, ListNamesOwnEntriesFirstAndLeavesOutThePeer) {
    const auto third = node_named("third");
    NodeList added;

    added.add(first_endpoint, first_id());
    added.add(second_endpoint, second_id());
    added.add(third_endpoint, third);

    const auto list = publisher().list_for(added, first_id());

    ASSERT_TRUE(list);
    EXPECT_EQ(named_in(*list),
              own_then({entry(second_endpoint, second_id()), entry(third_endpoint, third)}));
}

// The peer is new as this node has just reached it, so it has nothing to
// learn.
TEST_F(NodeListPublisherTest, ListIsNothingWhenOnlyThePeerIsNew) {
    NodeList added;

    added.add(first_endpoint, first_id());
    added.add(second_endpoint, first_id());

    EXPECT_FALSE(publisher().list_for(added, first_id()));
    EXPECT_FALSE(publisher().list_for(NodeList(), first_id()));
}

// This node's own entries are named once, first, whatever added names.
TEST_F(NodeListPublisherTest, ListLeavesOutEntriesNamingThisNode) {
    NodeList added;

    added.add(own_endpoint, second_id());
    added.add(second_endpoint, own_id());

    EXPECT_FALSE(publisher().list_for(added, first_id()));

    added.add(third_endpoint, second_id());

    const auto list = publisher().list_for(added, first_id());

    ASSERT_TRUE(list);
    EXPECT_EQ(named_in(*list), own_then({entry(third_endpoint, second_id())}));
}

TEST_F(NodeListPublisherTest, OwnEndpointsComeFirstInTheOrderGiven) {
    const NodeListPublisher publishing(own_id(), {"https://node.example", own_endpoint});
    NodeList added;

    added.add(second_endpoint, second_id());

    const auto list = publishing.list_for(added, first_id());

    ASSERT_TRUE(list);
    EXPECT_EQ(named_in(*list), (std::vector<std::string>{
                                   entry("https://node.example", own_id()),
                                   entry(own_endpoint, own_id()),
                                   entry(second_endpoint, second_id()),
                               }));
}

// Each peer is posted, signed as this node, what is new but itself.
TEST_F(NodeListPublisherTest, EachPeerIsPostedTheOthers) {
    const StandInNode first(ResponseCode::Status::Accepted, first_signer());
    const StandInNode second(ResponseCode::Status::Accepted, second_signer());
    const auto third = node_named("third");
    NodeList added;

    added.add(first_endpoint, first_id());
    added.add(second_endpoint, second_id());
    added.add(third_endpoint, third);

    const NodeListPublisher::Sessions sessions{
        session_with(first_id(), first.address()),
        session_with(second_id(), second.address()),
    };

    EXPECT_EQ(publisher().publish(added, sessions), 2U);

    const auto to_first = first.requests();
    const auto to_second = second.requests();

    ASSERT_EQ(to_first.size(), 1U);
    ASSERT_EQ(to_second.size(), 1U);
    EXPECT_EQ(to_first.at(0).method(), "POST");
    EXPECT_EQ(to_first.at(0).uri(), "/data/nodes");
    EXPECT_EQ(to_first.at(0).headers().get("Content-Type", ""), "application/json");
    EXPECT_TRUE(signed_by_this_node(to_first.at(0)));
    EXPECT_TRUE(signed_by_this_node(to_second.at(0)));
    EXPECT_EQ(to_first.at(0).body(), list_of({{second_endpoint, second_id()}, {third_endpoint, third}}));
    EXPECT_EQ(to_second.at(0).body(), list_of({{first_endpoint, first_id()}, {third_endpoint, third}}));
}

// The first request the peer is sent is the one after the publishing, so
// it was posted nothing.
TEST_F(NodeListPublisherTest, PeerThatAloneIsNewIsPostedNothing) {
    const StandInNode first(ResponseCode::Status::Accepted, first_signer());
    const auto session = session_with(first_id(), first.address());
    NodeList added;
    Request after;

    added.add(first_endpoint, first_id());
    after.set_method("GET");
    after.set_uri("/data/seek");

    EXPECT_EQ(publisher().publish(added, {session}), 0U);
    (void)session->exchange({after});

    const auto sent = first.requests();

    ASSERT_EQ(sent.size(), 1U);
    EXPECT_EQ(sent.at(0).uri(), "/data/seek");
}

TEST_F(NodeListPublisherTest, ListNotAcceptedIsLoggedAndNotCounted) {
    const StandInNode first(ResponseCode::Status::BadRequest, first_signer());
    const StandInNode second(ResponseCode::Status::Accepted, second_signer());
    NodeList added;

    added.add(third_endpoint, node_named("third"));

    const NodeListPublisher::Sessions sessions{
        session_with(first_id(), first.address()),
        session_with(second_id(), second.address()),
    };

    EXPECT_EQ(publisher().publish(added, sessions), 1U);
    EXPECT_EQ(first.requests().size(), 1U);
    EXPECT_EQ(second.requests().size(), 1U);
    EXPECT_TRUE(logged("was answered 400"));
}

// An answer that is not signed ends the session, which is logged, and the
// other peer is told all the same.
TEST_F(NodeListPublisherTest, PostThatFailsIsLoggedAndNotCounted) {
    const StandInNode first(ResponseCode::Status::Accepted, std::nullopt);
    const StandInNode second(ResponseCode::Status::Accepted, second_signer());
    const auto failing = session_with(first_id(), first.address());
    NodeList added;

    added.add(third_endpoint, node_named("third"));

    EXPECT_EQ(publisher().publish(added, {failing, session_with(second_id(), second.address())}),
              1U);
    EXPECT_FALSE(failing->is_open());
    EXPECT_TRUE(logged("Could not post a node list"));
}

TEST_F(NodeListPublisherTest, SessionsClosedOrMissingArePostedNothing) {
    const StandInNode first(ResponseCode::Status::Accepted, first_signer());
    const auto closed = session_with(first_id(), first.address());
    NodeList added;

    added.add(third_endpoint, node_named("third"));
    closed->close();

    EXPECT_EQ(publisher().publish(added, {closed, nullptr}), 0U);
    EXPECT_TRUE(first.requests().empty());
}

TEST_F(NodeListPublisherTest, NeedsAnIdAndEndpoints) {
    EXPECT_THROW((void)NodeListPublisher(Identifier(), {own_endpoint}), std::invalid_argument);
    EXPECT_THROW((void)NodeListPublisher(own_id(), {"ftp://localhost:8080"}), std::invalid_argument);
}
