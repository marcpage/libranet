#include <gtest/gtest.h>
#include <chrono>
#include <cstddef>
#include <exception>
#include <functional>
#include <memory>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <utility>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "identity/KnownKeys.hpp"
#include "identity/MessageSigner.hpp"
#include "identity/MessageVerifier.hpp"
#include "identity/NodeIdentity.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "peer/PeerConnection.hpp"
#include "peer/PeerSession.hpp"
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
using net::Address;
using net::ISocket;
using peer::PeerConnection;
using peer::PeerSession;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;

namespace {

// How long a node waits, at most, for this node to send something, so that
// a test fails rather than waits forever when it does not come.
constexpr auto patience = std::chrono::seconds(10);

// The header that gives a body's length, as a line of a message's head.
constexpr std::string_view length_line = "Content-Length: ";

// How a node answers a request, given as sent: with the text this gives.
using Answering = std::function<std::string(const std::string& request)>;

// The next request on socket, as sent: its line and headers, each ended
// with a CRLF, and its body, as long as its Content-Length gives. Nothing
// once the connection has closed.
std::optional<std::string> read_request(ISocket& socket) {
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

    return text + "\r\n" + Converter::text_of(socket.read_exact(length));
}

// A node on the loopback address that takes one connection, and answers each
// request on it in turn, on a thread of its own, until the connection
// closes.
class StandInNode {
public:
    explicit StandInNode(Answering answering);
    ~StandInNode();

    [[nodiscard]] Address address() const;

    StandInNode(const StandInNode&) = delete;
    StandInNode& operator=(const StandInNode&) = delete;
    StandInNode(StandInNode&&) = delete;
    StandInNode& operator=(StandInNode&&) = delete;

private:
    ISocket::Ptr m_listener;
    std::thread m_thread;
};

StandInNode::StandInNode(Answering answering)
    : m_listener(ISocket::bind(Address("127.0.0.1", 0))) {
    m_listener->listen(1);
    m_thread = std::thread([this, answering = std::move(answering)] {
        try {
            const auto connection = m_listener->accept();

            connection->set_receive_timeout(patience);

            while (const auto request = read_request(*connection)) {
                connection->send_all(answering(*request));
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

// An answer of 200 OK with the body, and its length.
Response ok(std::string body) {
    Response answer;

    answer.set_status_code(http::ResponseCode::Status::Ok);
    answer.headers().set("Content-Length", std::to_string(body.size()));
    answer.set_body(std::move(body));
    return answer;
}

// Answers each request with its text, as the body of an answer signer signs.
Answering signed_echo(const MessageSigner& signer) {
    return [signer](const std::string& request) {
        auto answer = ok(request);

        signer.sign(answer);
        return answer.to_string();
    };
}

// Answers each request with its text, unsigned.
std::string unsigned_echo(const std::string& request) {
    return ok(request).to_string();
}

// Answers each request with a body other than the one signer signed.
Answering changed_after_signing(const MessageSigner& signer) {
    return [signer](const std::string& /*request*/) {
        auto answer = ok("hello");

        signer.sign(answer);
        answer.set_body("jello");
        return answer.to_string();
    };
}

// Answers each request with an answer signer signs, saying Connection:
// close.
Answering turning_away(const MessageSigner& signer) {
    return [signer](const std::string& request) {
        auto answer = ok(request);

        answer.headers().set("Connection", "close");
        signer.sign(answer);
        return answer.to_string();
    };
}

// Answers each request with how many it has answered, this one counted, in
// an answer signer signs.
Answering counting(const MessageSigner& signer) {
    return [signer, answered = 0](const std::string& /*request*/) mutable {
        auto answer = ok(std::to_string(++answered));

        signer.sign(answer);
        return answer.to_string();
    };
}

// A request with the method, target, and body.
Request request(const std::string& method, const std::string& target,
                const std::string& body = "") {
    Request made;

    made.set_method(method);
    made.set_uri(target);
    made.set_body(body);
    return made;
}

// A verifier of the keys store holds.
MessageVerifier verifier_of(const IDataSource::Ptr& store) {
    return {std::make_shared<KnownKeys>(store), {}};
}

// Each test has this node, the peer it has a session with, and another
// node, each with a key of its own, and a store that holds their public
// keys.
class PeerSessionTest : public testing::Test {
protected:
    PeerSessionTest()
        : m_store(std::make_shared<fixtures::MemorySource>())
        , m_this_node(m_directory.path() / "this-node.pem", *m_store)
        , m_peer(m_directory.path() / "peer.pem", *m_store)
        , m_other(m_directory.path() / "other.pem", *m_store) {}

    // A session with the peer at address, whose answers are checked against
    // the keys keys holds.
    [[nodiscard]] PeerSession session_checking(const Address& address,
                                               const IDataSource::Ptr& keys) const {
        return {std::make_unique<PeerConnection>(address, PeerConnection::Limits{}), m_peer.id(),
                MessageSigner(m_this_node), verifier_of(keys)};
    }

    // A session with the peer at address, which holds the peer's key.
    [[nodiscard]] PeerSession session_with(const Address& address) const {
        return session_checking(address, m_store);
    }

    [[nodiscard]] const Identifier& peer_id() const {
        return m_peer.id();
    }

    [[nodiscard]] MessageSigner peer_signer() const {
        return MessageSigner(m_peer);
    }

    [[nodiscard]] MessageSigner other_signer() const {
        return MessageSigner(m_other);
    }

    // Whether the request echoed as answer's body was signed by this node,
    // with its key.
    [[nodiscard]] bool echoes_this_nodes(const Response& answer) const {
        std::istringstream text{std::string(answer.body())};
        Request echoed;

        text >> echoed;

        const auto signer = verifier_of(m_store).verify(echoed);

        return signer && signer->verified
               && signer->node_id.to_string() == m_this_node.id().to_string();
    }

private:
    fixtures::ScratchDirectory m_directory;
    std::shared_ptr<fixtures::MemorySource> m_store;
    NodeIdentity m_this_node;
    NodeIdentity m_peer;
    NodeIdentity m_other;
};

} // namespace

// The peer echoes each request, so each answer shows its request was signed
// as this node, and was given to its request.
TEST_F(PeerSessionTest, RequestsAreSignedAndAnswersGiven) {
    const StandInNode peer(signed_echo(peer_signer()));
    auto session = session_with(peer.address());

    const auto answers = session.exchange(
        {request("GET", "/data/seek"), request("POST", "/data/nodes", R"({"nodes": {}})")});

    ASSERT_EQ(answers.size(), 2U);
    EXPECT_EQ(answers.at(0).body().substr(0, 19), "GET /data/seek HTTP");
    EXPECT_EQ(answers.at(1).body().substr(0, 21), "POST /data/nodes HTTP");
    EXPECT_TRUE(echoes_this_nodes(answers.at(0)));
    EXPECT_TRUE(echoes_this_nodes(answers.at(1)));
    EXPECT_TRUE(session.is_open());
    EXPECT_FALSE(session.was_turned_away());
}

TEST_F(PeerSessionTest, UnsignedAnswerEndsTheSession) {
    const StandInNode peer(unsigned_echo);
    auto session = session_with(peer.address());

    EXPECT_THROW((void)session.exchange({request("GET", "/first"), request("GET", "/second")}),
                 PeerSession::Unauthenticated);
    EXPECT_FALSE(session.is_open());
    EXPECT_FALSE(session.was_turned_away());
    EXPECT_THROW((void)session.exchange({request("GET", "/third")}), PeerConnection::Closed);
}

TEST_F(PeerSessionTest, AnswerSignedByAnotherNodeEndsTheSession) {
    const StandInNode peer(signed_echo(other_signer()));
    auto session = session_with(peer.address());

    EXPECT_THROW((void)session.exchange({request("GET", "/data/seek")}),
                 PeerSession::Unauthenticated);
    EXPECT_FALSE(session.is_open());
}

TEST_F(PeerSessionTest, AnswerWhoseSignatureFailsEndsTheSession) {
    const StandInNode peer(changed_after_signing(peer_signer()));
    auto session = session_with(peer.address());

    EXPECT_THROW((void)session.exchange({request("GET", "/data/seek")}),
                 PeerSession::Unauthenticated);
    EXPECT_FALSE(session.is_open());
}

// The peer's key should have been fetched before the session began
// (Handshake Protocol §3), so an answer that cannot be checked with it fails.
TEST_F(PeerSessionTest, AnswerFromAPeerWhoseKeyIsNotHeldEndsTheSession) {
    const StandInNode peer(signed_echo(peer_signer()));
    auto session = session_checking(peer.address(), std::make_shared<fixtures::MemorySource>());

    EXPECT_THROW((void)session.exchange({request("GET", "/data/seek")}),
                 PeerSession::Unauthenticated);
    EXPECT_FALSE(session.is_open());
}

TEST_F(PeerSessionTest, AnswerTurningThisNodeAwayIsGiven) {
    const StandInNode peer(turning_away(peer_signer()));
    auto session = session_with(peer.address());

    const auto answers = session.exchange({request("GET", "/data/nodes")});

    ASSERT_EQ(answers.size(), 1U);
    EXPECT_EQ(answers.at(0).body().substr(0, 20), "GET /data/nodes HTTP");
    EXPECT_TRUE(session.was_turned_away());
    EXPECT_FALSE(session.is_open());
}

// The request that could not be signed left the one before it unsent, so
// the peer's first answer is to the request after.
TEST_F(PeerSessionTest, RequestThatCannotBeSignedSendsNone) {
    const StandInNode peer(counting(peer_signer()));
    auto session = session_with(peer.address());

    EXPECT_THROW((void)session.exchange({request("GET", "/data/seek"), request("GET", "data")}),
                 std::invalid_argument);
    EXPECT_TRUE(session.is_open());

    const auto answers = session.exchange({request("GET", "/data/nodes")});

    ASSERT_EQ(answers.size(), 1U);
    EXPECT_EQ(answers.at(0).body(), "1");
}

TEST_F(PeerSessionTest, CloseEndsTheSession) {
    const StandInNode peer(signed_echo(peer_signer()));
    auto session = session_with(peer.address());

    EXPECT_EQ(session.node_id().to_string(), peer_id().to_string());
    EXPECT_EQ(session.address().to_string(), peer.address().to_string());

    session.close();

    EXPECT_FALSE(session.is_open());
    EXPECT_FALSE(session.was_turned_away());
    EXPECT_THROW((void)session.exchange({request("GET", "/data/seek")}), PeerConnection::Closed);
}

TEST_F(PeerSessionTest, NeedsAConnectionAndANodeId) {
    const StandInNode peer(unsigned_echo);
    const auto keys = std::make_shared<fixtures::MemorySource>();
    auto connection = std::make_unique<PeerConnection>(peer.address(), PeerConnection::Limits{});

    EXPECT_THROW((void)PeerSession(nullptr, peer_id(), peer_signer(), verifier_of(keys)),
                 std::invalid_argument);
    EXPECT_THROW(
        (void)PeerSession(std::move(connection), Identifier(), peer_signer(), verifier_of(keys)),
        std::invalid_argument);
}
