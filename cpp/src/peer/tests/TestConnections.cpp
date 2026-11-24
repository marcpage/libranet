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
#include "msg/Message.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "peer/Connections.hpp"
#include "protocol/NodeList.hpp"
#include "stats/NodeAddresses.hpp"
#include "stats/Recorder.hpp"
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
using peer::Connections;
using protocol::NodeList;
using stats::Recorder;
using storage::Identifier;
using types::Converter;
using namespace std::chrono_literals;
using Candidate = stats::NodeAddresses::Candidate;
using CandidatesUpdated = Recorder::CandidatesUpdated;
using ConnectionFailed = Recorder::ConnectionFailed;
using ConnectionOpened = Recorder::ConnectionOpened;
using NodesUpdated = Recorder::NodesUpdated;
using PeersConnected = Connections::PeersConnected;
using Seed = Connections::Seed;
using Status = http::ResponseCode::Status;

// The tests take these, which connections post from another file, so nothing
// here would make their messages' virtual to_string(), which next<>() casts
// to.
template class msg::Message<ConnectionFailed>;
template class msg::Message<ConnectionOpened>;
template class msg::Message<PeersConnected>;

namespace {

// How long a test waits, at most, for what it expects, so that it fails
// rather than waits forever when it does not come.
constexpr auto patience = std::chrono::seconds(10);
// How long a test waits for what it expects not to come.
constexpr auto quiet = 300ms;
// How often a test looks again for what it waits for.
constexpr auto poll_interval = 10ms;
// How often connections are checked in the tests: often, so that a test
// waits little for what a check finds.
constexpr auto check_interval = 10ms;

// The header that gives a body's length, as a line of a message's head.
constexpr std::string_view length_line = "Content-Length: ";

// The endpoints this node names itself at, and another node at.
constexpr auto own_endpoint = "http://198.51.100.1:4300";
constexpr auto other_endpoint = "http://203.0.113.7:4300";

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

// The request text holds, as sent.
Request parsed(const std::string& text) {
    std::istringstream stream(text);
    Request request;

    stream >> request;
    return request;
}

// The request's method and target.
std::string line_of(const Request& request) {
    return std::string(request.method()) + " " + std::string(request.uri());
}

// An answer of status with the body, and its length.
Response answer(Status status, std::string body = "") {
    Response made;

    made.set_status_code(status);
    made.headers().set("Content-Length", std::to_string(body.size()));
    made.set_body(std::move(body));
    return made;
}

// Where the content identifier names is.
std::string data_path(const Identifier& identifier) {
    return "/data/" + identifier.to_string();
}

// The endpoint of address, on the loopback address.
std::string endpoint_of(const Address& address) {
    return "http://127.0.0.1:" + std::to_string(address.port());
}

// A socket on the loopback address that takes no connection, so that
// nothing answers at its endpoint while it is held.
ISocket::Ptr refusing() {
    return ISocket::bind(Address("127.0.0.1", 0));
}

// The id of each node of ids, in order.
std::vector<std::string> texts_of(const std::vector<Identifier>& ids) {
    std::vector<std::string> texts;

    std::transform(ids.begin(), ids.end(), std::back_inserter(texts),
                   [](const Identifier& node_id) { return node_id.to_string(); });
    return texts;
}

// The endpoint and node id of each entry of list, in order.
std::vector<std::string> entries_of(const NodeList& list) {
    std::vector<std::string> entries;

    std::transform(list.entries().begin(), list.entries().end(), std::back_inserter(entries),
                   [](const NodeList::Entry& entry) {
        return entry.endpoint + "=" + entry.node_id.to_string();
    });
    return entries;
}

// Waits for condition, and says whether it came within patience.
template<typename Condition>
bool eventually(const Condition& condition) {
    const auto until = std::chrono::steady_clock::now() + patience;

    while (!condition()) {
        if (std::chrono::steady_clock::now() >= until) {
            return false;
        }

        std::this_thread::sleep_for(poll_interval);
    }

    return true;
}

// A node on the loopback address that answers as identity, which must
// outlive it, on a thread of its own, one connection after another. Each
// answer is signed: a PUT is 201, a GET of its key is the key, a POST of a
// node list is 202, and anything else 404. It keeps each request it is sent.
class StandInNode {
public:
    explicit StandInNode(const NodeIdentity& identity);
    ~StandInNode();
    StandInNode(const StandInNode&) = delete;
    StandInNode& operator=(const StandInNode&) = delete;
    StandInNode(StandInNode&&) = delete;
    StandInNode& operator=(StandInNode&&) = delete;

    [[nodiscard]] std::string endpoint() const;
    // The requests it was sent, in the order they came.
    [[nodiscard]] std::vector<Request> requests() const;
    // The method and target of each request it was sent, in order.
    [[nodiscard]] std::vector<std::string> asked() const;
    // How many connections it took.
    [[nodiscard]] std::size_t accepted() const;
    // Ends the connection it is serving, if any.
    void hang_up();

private:
    const NodeIdentity* m_identity;
    MessageSigner m_signer;
    ISocket::Ptr m_listener;
    mutable std::mutex m_mutex;
    std::vector<Request> m_requests; // Guarded by m_mutex
    std::size_t m_accepted = 0;      // Guarded by m_mutex
    ISocket::Ptr m_connection;       // Guarded by m_mutex: the one served now
    bool m_stopping = false;         // Guarded by m_mutex
    std::thread m_thread;

    void _serve(ISocket& connection);
    [[nodiscard]] Response _answer(const Request& request) const;
};

StandInNode::StandInNode(const NodeIdentity& identity)
    : m_identity(&identity)
    , m_signer(identity)
    , m_listener(ISocket::bind(Address("127.0.0.1", 0))) {
    m_listener->listen(4);
    m_thread = std::thread([this] {
        while (true) {
            ISocket::Ptr connection;

            try {
                connection = m_listener->accept();
            } catch (const std::exception& error) {
                ADD_FAILURE() << "The node failed to take a connection: " << error.what();
                return;
            }

            ISocket* serving = nullptr;

            {
                const std::scoped_lock lock(m_mutex);

                if (m_stopping) {
                    return;
                }

                ++m_accepted;
                m_connection = std::move(connection);
                serving = m_connection.get();
            }

            _serve(*serving);

            // Closed holding the lock, so that hang_up() never ends a
            // connection as it closes.
            const std::scoped_lock lock(m_mutex);
            m_connection.reset();
        }
    });
}

StandInNode::~StandInNode() {
    {
        const std::scoped_lock lock(m_mutex);

        m_stopping = true;

        if (m_connection) {
            m_connection->shutdown();
        }
    }

    // A connection wakes the thread waiting to take one, to see it is to
    // stop.
    try {
        (void)ISocket::connect(m_listener->local_address(), patience);
    } catch (const std::exception& error) {
        ADD_FAILURE() << "Could not wake the node: " << error.what();
    }

    m_thread.join();
}

std::string StandInNode::endpoint() const {
    return endpoint_of(m_listener->local_address());
}

std::vector<Request> StandInNode::requests() const {
    const std::scoped_lock lock(m_mutex);

    return m_requests;
}

std::vector<std::string> StandInNode::asked() const {
    const auto sent = requests();
    std::vector<std::string> lines;

    std::transform(sent.begin(), sent.end(), std::back_inserter(lines), &line_of);
    return lines;
}

std::size_t StandInNode::accepted() const {
    const std::scoped_lock lock(m_mutex);

    return m_accepted;
}

void StandInNode::hang_up() {
    const std::scoped_lock lock(m_mutex);

    if (m_connection) {
        m_connection->shutdown();
    }
}

// Answers each request on connection until it closes, is hung up, or is
// idle too long.
void StandInNode::_serve(ISocket& connection) {
    try {
        connection.set_receive_timeout(patience);

        while (const auto text = read_request(connection)) {
            auto request = parsed(*text);
            const auto reply = _answer(request);

            {
                const std::scoped_lock lock(m_mutex);
                m_requests.push_back(std::move(request));
            }

            connection.send_all(reply.to_string());
        }
        // Connections end the connection as they go, or it was hung up.
        // NOLINTNEXTLINE(bugprone-empty-catch)
    } catch (const std::exception&) {
    }
}

Response StandInNode::_answer(const Request& request) const {
    const auto line = line_of(request);
    auto reply = answer(Status::NotFound);

    if ("PUT" == request.method()) {
        reply = answer(Status::Created);
    } else if ("GET " + data_path(m_identity->id()) == line) {
        reply = answer(Status::Ok, m_identity->public_pem());
    } else if ("POST /data/nodes" == line) {
        reply = answer(Status::Accepted);
    }

    m_signer.sign(reply);
    return reply;
}

// Each test has this node, with a store that holds its key, and two other
// nodes, whose keys are in a store of their own, so that first contact
// fetches them.
class ConnectionsTest : public testing::Test {
protected:
    ConnectionsTest()
        : m_store(std::make_shared<fixtures::MemorySource>())
        , m_peer_store(std::make_shared<fixtures::MemorySource>())
        , m_this_node(m_directory.path() / "this-node.pem", *m_store)
        , m_peer(m_directory.path() / "peer.pem", *m_peer_store)
        , m_other(m_directory.path() / "other.pem", *m_peer_store) {}

    // The settings the tests use: a node that fails, or whose connection
    // closes, rests longer than any test runs, and connections are checked
    // often.
    [[nodiscard]] static Connections::Settings settings(std::vector<Seed> seeds = {}) {
        Connections::Settings made;

        made.rests.retry_delay = 1h;
        made.rests.turned_away_rest = 1h;
        made.limits.connect_timeout = 2s;
        made.limits.request_timeout = patience;
        made.seeds = std::move(seeds);
        made.check_interval = check_interval;
        return made;
    }

    // Connections of this node, naming itself at own_endpoint.
    [[nodiscard]] std::unique_ptr<Connections> connections(std::vector<Seed> seeds = {}) const {
        return connections_with(settings(std::move(seeds)));
    }

    [[nodiscard]] std::unique_ptr<Connections>
    connections_with(Connections::Settings with) const {
        return std::make_unique<Connections>(m_this_node, std::vector<std::string>{own_endpoint},
                                             m_store, verifier(), std::move(with));
    }

    [[nodiscard]] MessageVerifier verifier() const {
        return {std::make_shared<KnownKeys>(m_store), {}};
    }

    // The request putting this node's key, and the one getting node's.
    [[nodiscard]] std::string put_line() const {
        return "PUT " + data_path(m_this_node.id());
    }

    [[nodiscard]] static std::string key_line(const NodeIdentity& node) {
        return "GET " + data_path(node.id());
    }

    [[nodiscard]] const NodeIdentity& this_node() const {
        return m_this_node;
    }

    [[nodiscard]] const NodeIdentity& peer() const {
        return m_peer;
    }

    [[nodiscard]] const NodeIdentity& other() const {
        return m_other;
    }

private:
    fixtures::ScratchDirectory m_directory;
    std::shared_ptr<fixtures::MemorySource> m_store;
    std::shared_ptr<fixtures::MemorySource> m_peer_store;
    NodeIdentity m_this_node;
    NodeIdentity m_peer;
    NodeIdentity m_other;
};

// Posts the candidates, as stats::Recorder does.
void post_candidates(std::vector<Candidate> candidates) {
    Channel::post(CandidatesUpdated::name, CandidatesUpdated{std::move(candidates)});
}

// The node list posted in each POST /data/nodes node was sent, in order.
std::vector<NodeList> lists_posted(const StandInNode& node) {
    std::vector<NodeList> lists;

    for (const auto& request : node.requests()) {
        if ("POST /data/nodes" == line_of(request)) {
            lists.push_back(NodeList::from_body(Converter::bytes_of(request.body())));
        }
    }

    return lists;
}

} // namespace

// NOLINTBEGIN(bugprone-unchecked-optional-access)

TEST_F(ConnectionsTest, NoPeersAreConnectedAsItStarts) {
    Channel connected(PeersConnected::name);
    const auto made = connections();
    const auto posted = connected.next<PeersConnected>(patience);

    ASSERT_TRUE(posted.has_value());
    EXPECT_TRUE(posted->node_ids.empty());
}

TEST_F(ConnectionsTest, CheckIntervalOfZeroIsRefused) {
    auto refused = settings();

    refused.check_interval = 0ms;
    EXPECT_THROW(Connections(this_node(), {own_endpoint}, std::make_shared<fixtures::MemorySource>(),
                             verifier(), refused),
                 std::invalid_argument);
}

TEST_F(ConnectionsTest, CandidateIsDialedAndFirstContactMade) {
    const StandInNode node(peer());
    Channel opened(ConnectionOpened::name);
    Channel connected(PeersConnected::name);
    const auto made = connections();

    post_candidates({{peer().id(), {node.endpoint()}}});

    const auto reached = opened.next<ConnectionOpened>(patience);
    const auto first = connected.next<PeersConnected>(patience);
    const auto then = connected.next<PeersConnected>(patience);

    ASSERT_TRUE(reached.has_value());
    EXPECT_EQ(reached->node_id.to_string(), peer().id().to_string());
    EXPECT_EQ(reached->endpoint, node.endpoint());
    ASSERT_TRUE(first.has_value());
    EXPECT_TRUE(first->node_ids.empty());
    ASSERT_TRUE(then.has_value());
    EXPECT_EQ(texts_of(then->node_ids), std::vector<std::string>{peer().id().to_string()});
    EXPECT_EQ(node.asked(), (std::vector<std::string>{put_line(), key_line(peer()),
                                                      "POST /data/nodes", "GET /data/seek",
                                                      "GET /data/nodes"}));
}

TEST_F(ConnectionsTest, NothingIsDialedBeforeTheCandidatesCome) {
    const StandInNode node(peer());
    const auto made = connections({{node.endpoint()}});

    std::this_thread::sleep_for(quiet);
    EXPECT_EQ(node.accepted(), 0U);
}

TEST_F(ConnectionsTest, OwnEndpointsArePostedBeforeTheNodeList) {
    const StandInNode node(peer());
    Channel opened(ConnectionOpened::name);
    const auto made = connections();

    post_candidates({{peer().id(), {node.endpoint()}}});
    ASSERT_TRUE(opened.next<ConnectionOpened>(patience).has_value());

    const auto posted = lists_posted(node);

    ASSERT_EQ(posted.size(), 1U);
    EXPECT_EQ(entries_of(posted.front()),
              std::vector<std::string>{std::string(own_endpoint) + "=" + this_node().id().to_string()});
}

TEST_F(ConnectionsTest, NodeListIsPostedAtFirstContact) {
    const StandInNode node(peer());
    Channel opened(ConnectionOpened::name);
    const auto made = connections();
    NodeList nodes(this_node().id(), {own_endpoint});

    nodes.add(other_endpoint, other().id());
    Channel::post(NodesUpdated::name, NodesUpdated{nodes, nodes});
    post_candidates({{peer().id(), {node.endpoint()}}});
    ASSERT_TRUE(opened.next<ConnectionOpened>(patience).has_value());

    const auto posted = lists_posted(node);

    ASSERT_EQ(posted.size(), 1U);
    EXPECT_EQ(entries_of(posted.front()), entries_of(nodes));
}

TEST_F(ConnectionsTest, EntriesNewlyNamedArePostedToThePeers) {
    const StandInNode node(peer());
    Channel opened(ConnectionOpened::name);
    const auto made = connections();
    NodeList added;

    post_candidates({{peer().id(), {node.endpoint()}}});
    ASSERT_TRUE(opened.next<ConnectionOpened>(patience).has_value());

    added.add(other_endpoint, other().id());
    Channel::post(NodesUpdated::name, NodesUpdated{added, added});
    ASSERT_TRUE(eventually([&node] { return lists_posted(node).size() >= 2; }));

    EXPECT_EQ(entries_of(lists_posted(node).at(1)),
              (std::vector<std::string>{
                  std::string(own_endpoint) + "=" + this_node().id().to_string(),
                  std::string(other_endpoint) + "=" + other().id().to_string()}));
}

TEST_F(ConnectionsTest, EndpointsAreTriedInTurn) {
    const StandInNode node(peer());
    const auto refused = refusing();
    const auto unanswered = endpoint_of(refused->local_address());
    Channel failed(ConnectionFailed::name);
    Channel opened(ConnectionOpened::name);
    const auto made = connections();

    post_candidates({{peer().id(), {unanswered, node.endpoint()}}});

    const auto missed = failed.next<ConnectionFailed>(patience);
    const auto reached = opened.next<ConnectionOpened>(patience);

    ASSERT_TRUE(missed.has_value());
    EXPECT_EQ(missed->node_id.to_string(), peer().id().to_string());
    EXPECT_EQ(missed->endpoint, unanswered);
    ASSERT_TRUE(reached.has_value());
    EXPECT_EQ(reached->node_id.to_string(), peer().id().to_string());
    EXPECT_EQ(reached->endpoint, node.endpoint());
}

TEST_F(ConnectionsTest, NodeNotReachedRests) {
    const auto refused = refusing();
    const auto unanswered = endpoint_of(refused->local_address());
    Channel failed(ConnectionFailed::name);
    const auto made = connections();

    post_candidates({{peer().id(), {unanswered}}});
    ASSERT_TRUE(failed.next<ConnectionFailed>(patience).has_value());

    post_candidates({{peer().id(), {unanswered}}});
    EXPECT_FALSE(failed.next<ConnectionFailed>(quiet).has_value());
}

TEST_F(ConnectionsTest, AnotherNodeAnsweringFailsTheNodeDialed) {
    const StandInNode node(peer());
    Channel failed(ConnectionFailed::name);
    Channel opened(ConnectionOpened::name);
    const auto made = connections();

    post_candidates({{other().id(), {node.endpoint()}}});

    const auto missed = failed.next<ConnectionFailed>(patience);
    const auto reached = opened.next<ConnectionOpened>(patience);

    ASSERT_TRUE(missed.has_value());
    EXPECT_EQ(missed->node_id.to_string(), other().id().to_string());
    EXPECT_EQ(missed->endpoint, node.endpoint());
    ASSERT_TRUE(reached.has_value());
    EXPECT_EQ(reached->node_id.to_string(), peer().id().to_string());
    EXPECT_EQ(reached->endpoint, node.endpoint());
}

TEST_F(ConnectionsTest, ThisNodeIsNotTakenIn) {
    const StandInNode node(this_node());
    Channel failed(ConnectionFailed::name);
    Channel opened(ConnectionOpened::name);
    Channel connected(PeersConnected::name);
    const auto made = connections();

    post_candidates({{peer().id(), {node.endpoint()}}});

    const auto missed = failed.next<ConnectionFailed>(patience);

    ASSERT_TRUE(missed.has_value());
    EXPECT_EQ(missed->node_id.to_string(), peer().id().to_string());
    EXPECT_FALSE(opened.next<ConnectionOpened>(quiet).has_value());
    ASSERT_TRUE(connected.next<PeersConnected>(patience).has_value());
    EXPECT_FALSE(connected.next<PeersConnected>(quiet).has_value());
}

TEST_F(ConnectionsTest, ClosedConnectionLeavesThePeersAndRests) {
    StandInNode node(peer());
    Channel opened(ConnectionOpened::name);
    Channel connected(PeersConnected::name);
    const auto made = connections();

    post_candidates({{peer().id(), {node.endpoint()}}});
    ASSERT_TRUE(opened.next<ConnectionOpened>(patience).has_value());
    ASSERT_TRUE(connected.next<PeersConnected>(patience).has_value());
    ASSERT_TRUE(connected.next<PeersConnected>(patience).has_value());

    node.hang_up();

    const auto left = connected.next<PeersConnected>(patience);

    ASSERT_TRUE(left.has_value());
    EXPECT_TRUE(left->node_ids.empty());

    post_candidates({{peer().id(), {node.endpoint()}}});
    std::this_thread::sleep_for(quiet);
    EXPECT_EQ(node.accepted(), 1U);
}

TEST_F(ConnectionsTest, SeedsAreDialedWhileNoNodeIsKnown) {
    const StandInNode node(peer());
    Channel opened(ConnectionOpened::name);
    const auto made = connections({{node.endpoint()}});

    post_candidates({});

    const auto reached = opened.next<ConnectionOpened>(patience);

    ASSERT_TRUE(reached.has_value());
    EXPECT_EQ(reached->node_id.to_string(), peer().id().to_string());
    EXPECT_EQ(reached->endpoint, node.endpoint());
}

TEST_F(ConnectionsTest, SeedsAreNotDialedWhileANodeIsKnown) {
    const StandInNode seed(other());
    const StandInNode node(peer());
    Channel opened(ConnectionOpened::name);
    const auto made = connections({{seed.endpoint()}});

    post_candidates({{peer().id(), {node.endpoint()}}});

    const auto reached = opened.next<ConnectionOpened>(patience);

    ASSERT_TRUE(reached.has_value());
    EXPECT_EQ(reached->node_id.to_string(), peer().id().to_string());
    std::this_thread::sleep_for(quiet);
    EXPECT_EQ(seed.accepted(), 0U);
}

// Only a seed whose node id is known has a node to fail.
TEST_F(ConnectionsTest, SeedNotReachedFailsOnlyAKnownNode) {
    const auto refused_known = refusing();
    const auto refused_unknown = refusing();
    const auto known = endpoint_of(refused_known->local_address());
    const auto unknown = endpoint_of(refused_unknown->local_address());
    Channel failed(ConnectionFailed::name);
    const auto made = connections({{known, peer().id()}, {unknown}});

    post_candidates({});

    const auto missed = failed.next<ConnectionFailed>(patience);

    ASSERT_TRUE(missed.has_value());
    EXPECT_EQ(missed->node_id.to_string(), peer().id().to_string());
    EXPECT_EQ(missed->endpoint, known);
    EXPECT_FALSE(failed.next<ConnectionFailed>(quiet).has_value());
}

TEST_F(ConnectionsTest, SessionsAreThoseOfTheNodesConnected) {
    StandInNode node(peer());
    Channel connected(PeersConnected::name);
    const auto made = connections();

    EXPECT_TRUE(made->open_sessions().empty());
    EXPECT_TRUE(made->connected_ids().empty());

    post_candidates({{peer().id(), {node.endpoint()}}});
    ASSERT_TRUE(connected.next<PeersConnected>(patience).has_value());
    ASSERT_TRUE(connected.next<PeersConnected>(patience).has_value());

    const auto sessions = made->open_sessions();

    ASSERT_EQ(sessions.size(), 1U);
    EXPECT_EQ(sessions.front()->node_id().to_string(), peer().id().to_string());
    EXPECT_TRUE(sessions.front()->is_open());
    EXPECT_EQ(texts_of(made->connected_ids()), std::vector<std::string>{peer().id().to_string()});

    node.hang_up();

    const auto left = connected.next<PeersConnected>(patience);

    ASSERT_TRUE(left.has_value());
    EXPECT_TRUE(left->node_ids.empty());
    EXPECT_TRUE(made->open_sessions().empty());
    EXPECT_TRUE(made->connected_ids().empty());
}

// Each node's seek list is read at first contact, and again every
// peers.seek_refresh_seconds while its connection lasts (Handshake Protocol
// §3.3).
TEST_F(ConnectionsTest, SeekListsAreReadAgainWhileConnected) {
    const StandInNode node(peer());
    Channel opened(ConnectionOpened::name);
    auto often = settings();

    often.fulfiller.refresh = 20ms;

    const auto made = connections_with(often);
    const auto seek_reads = [&node] {
        const auto asked = node.asked();

        return std::count(asked.begin(), asked.end(), "GET /data/seek");
    };

    post_candidates({{peer().id(), {node.endpoint()}}});
    ASSERT_TRUE(opened.next<ConnectionOpened>(patience).has_value());
    EXPECT_TRUE(eventually([&seek_reads] { return seek_reads() >= 3; })) << seek_reads();
}

// Stopping closes the connections, so that nothing waits on them.
TEST_F(ConnectionsTest, StoppingClosesTheConnections) {
    const StandInNode node(peer());
    Channel connected(PeersConnected::name);
    auto made = connections();

    post_candidates({{peer().id(), {node.endpoint()}}});
    ASSERT_TRUE(connected.next<PeersConnected>(patience).has_value());
    ASSERT_TRUE(connected.next<PeersConnected>(patience).has_value());

    const auto sessions = made->open_sessions();

    ASSERT_EQ(sessions.size(), 1U);
    made.reset();
    EXPECT_FALSE(sessions.front()->is_open());
}

// NOLINTEND(bugprone-unchecked-optional-access)
