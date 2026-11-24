#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <cstddef>
#include <exception>
#include <iterator>
#include <map>
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
#include "msg/Message.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "peer/FirstContact.hpp"
#include "peer/PeerConnection.hpp"
#include "peer/PeerSession.hpp"
#include "peer/SeekFulfiller.hpp"
#include "protocol/NodeList.hpp"
#include "protocol/SeekList.hpp"
#include "stats/NodeAddresses.hpp"
#include "stats/Recorder.hpp"
#include "storage/DataStored.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "storage/Zlib.hpp"
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
using peer::FirstContact;
using peer::PeerConnection;
using peer::PeerSession;
using peer::SeekFulfiller;
using protocol::NodeList;
using protocol::SeekList;
using stats::Recorder;
using storage::DataStored;
using storage::HashPrefix;
using storage::IDataSource;
using storage::Identifier;
using storage::Zlib;
using types::Converter;
using namespace std::chrono_literals;
using NodesReceived = Recorder::NodesReceived;
using Source = stats::NodeAddresses::Source;
using Status = http::ResponseCode::Status;

// The tests take NodesReceived and DataStored, which first contact posts from
// another file, so nothing here would make their messages' virtual
// to_string(), which next<>() casts to.
template class msg::Message<NodesReceived>;
template class msg::Message<DataStored>;

namespace {

// How long a node waits, at most, for this node to send something, so that
// a test fails rather than waits forever when it does not come.
constexpr auto patience = std::chrono::seconds(10);

// The header that gives a body's length, as a line of a message's head.
constexpr std::string_view length_line = "Content-Length: ";

// The endpoints this node names itself at, and the peer at, in their lists.
constexpr auto own_endpoint = "http://198.51.100.1:4300";
constexpr auto peer_localhost = "http://localhost:4300";
constexpr auto peer_loopback = "http://127.0.0.1:4301";
constexpr auto other_endpoint = "http://203.0.113.7:4300";
// The peer's localhost endpoint, once resolved to the host dialed.
constexpr auto peer_resolved = "http://127.0.0.1:4300";

// How a node answers each request, by its method and target: with the
// answer given, as it is. One for a request named nowhere here is 404.
using Answers = std::map<std::string, Response>;

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

// The request's method and target, as Answers names a request.
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

// unsigned_answer, signed by signer.
Response signed_by(const MessageSigner& signer, Response unsigned_answer) {
    signer.sign(unsigned_answer);
    return unsigned_answer;
}

// Where the content identifier names is.
std::string data_path(const Identifier& identifier) {
    return "/data/" + identifier.to_string();
}

// A verifier of the keys store holds.
MessageVerifier verifier_of(const IDataSource::Ptr& store) {
    return {std::make_shared<KnownKeys>(store), {}};
}

// A node on the loopback address that takes one connection, and answers each
// request on it from its answers, on a thread of its own, until the
// connection closes. It keeps each request it is sent.
class StandInNode {
public:
    explicit StandInNode(Answers answers);
    ~StandInNode();

    [[nodiscard]] Address address() const;
    // The requests it was sent, in the order they came.
    [[nodiscard]] std::vector<Request> requests() const;
    // The method and target of each request it was sent, in order.
    [[nodiscard]] std::vector<std::string> asked() const;

    StandInNode(const StandInNode&) = delete;
    StandInNode& operator=(const StandInNode&) = delete;
    StandInNode(StandInNode&&) = delete;
    StandInNode& operator=(StandInNode&&) = delete;

private:
    ISocket::Ptr m_listener;
    mutable std::mutex m_mutex;
    std::vector<Request> m_requests; // Guarded by m_mutex
    std::thread m_thread;
};

StandInNode::StandInNode(Answers answers)
    : m_listener(ISocket::bind(Address("127.0.0.1", 0))) {
    m_listener->listen(1);
    m_thread = std::thread([this, answers = std::move(answers)] {
        try {
            const auto connection = m_listener->accept();

            connection->set_receive_timeout(patience);

            while (const auto text = read_request(*connection)) {
                auto request = parsed(*text);
                const auto found = answers.find(line_of(request));

                {
                    const std::scoped_lock lock(m_mutex);

                    m_requests.push_back(std::move(request));
                }

                connection->send_all(answers.end() == found ? answer(Status::NotFound).to_string()
                                                            : found->second.to_string());
            }
            // This node ends the connection while answers may be on their
            // way, as it does once one fails its check.
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
    const std::scoped_lock lock(m_mutex);

    return m_requests;
}

std::vector<std::string> StandInNode::asked() const {
    const auto sent = requests();
    std::vector<std::string> lines;

    std::transform(sent.begin(), sent.end(), std::back_inserter(lines), &line_of);
    return lines;
}

// Each test has this node, with a store that holds its key and another
// node's, and the peer it makes first contact with, whose key is in a store
// of the peer's own.
class FirstContactTest : public testing::Test {
protected:
    FirstContactTest()
        : m_store(std::make_shared<fixtures::MemorySource>())
        , m_peer_store(std::make_shared<fixtures::MemorySource>())
        , m_this_node(m_directory.path() / "this-node.pem", *m_store)
        , m_peer(m_directory.path() / "peer.pem", *m_peer_store)
        , m_other(m_directory.path() / "other.pem", *m_store) {}

    // First contact with the node at address, posting own_list(), as this
    // node seeks what own_seek names.
    [[nodiscard]] FirstContact::Contact contact_at(const Address& address,
                                                   const SeekList& own_seek = {}) const {
        const FirstContact first_contact(m_this_node, m_store, verifier_of(m_store), fulfiller());

        return first_contact.make(
            std::make_unique<PeerConnection>(address, PeerConnection::Limits{}), own_list(),
            own_seek);
    }

    // A fulfiller of what peers seek from this node's store, with the
    // settings the Python node's are by default.
    [[nodiscard]] std::shared_ptr<SeekFulfiller> fulfiller() const {
        return std::make_shared<SeekFulfiller>(m_store, SeekFulfiller::Settings{});
    }

    // How the peer answers, each answer signed by it: it takes this node's
    // key and list, sends its own key, and its seek list and node list.
    [[nodiscard]] Answers peer_answers() const {
        const auto signer = peer_signer();

        return {
            {put_line(), signed_by(signer, answer(Status::Created))},
            {key_line(), signed_by(signer, answer(Status::Ok, m_peer.public_pem()))},
            {"POST /data/nodes", signed_by(signer, answer(Status::Accepted))},
            {"GET /data/seek", signed_by(signer, answer(Status::Ok, peer_seek_text()))},
            {"GET /data/nodes", signed_by(signer, answer(Status::Ok, peer_list().to_string()))},
        };
    }

    // The node list this node posts.
    [[nodiscard]] NodeList own_list() const {
        return {m_this_node.id(), {own_endpoint}};
    }

    // The peer's node list: itself, at localhost and at a loopback address,
    // the other node, and this node.
    [[nodiscard]] NodeList peer_list() const {
        NodeList list(m_peer.id(), {peer_localhost, peer_loopback});

        list.add(other_endpoint, m_other.id());
        list.add(own_endpoint, m_this_node.id());
        return list;
    }

    // What the peer seeks: "hello", and what begins with abcdef, which no
    // key the tests make is likely to: one in 2^24 shares as many bits.
    [[nodiscard]] static SeekList peer_seek() {
        return {{Identifier("sha256", fixtures::hello_hash)}, {HashPrefix::parse("abcdef")}};
    }

    [[nodiscard]] static std::string peer_seek_text() {
        return Converter::text_of(peer_seek().write());
    }

    // The request putting this node's key, and the one getting the peer's.
    [[nodiscard]] std::string put_line() const {
        return "PUT " + data_path(m_this_node.id());
    }

    [[nodiscard]] std::string key_line() const {
        return "GET " + data_path(m_peer.id());
    }

    // This node holds the peer's key.
    void hold_peer_key() const {
        m_store->put(m_peer.id(), Converter::bytes_of(m_peer.public_pem()));
    }

    [[nodiscard]] bool holds_peer_key() const {
        return m_store->has(m_peer.id());
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

    [[nodiscard]] MessageSigner peer_signer() const {
        return MessageSigner(m_peer);
    }

    [[nodiscard]] MessageSigner other_signer() const {
        return MessageSigner(m_other);
    }

    [[nodiscard]] MessageVerifier verifier() const {
        return verifier_of(m_store);
    }

    [[nodiscard]] IDataSource::Ptr store() const {
        return m_store;
    }

private:
    fixtures::ScratchDirectory m_directory;
    std::shared_ptr<fixtures::MemorySource> m_store;
    std::shared_ptr<fixtures::MemorySource> m_peer_store;
    NodeIdentity m_this_node;
    NodeIdentity m_peer;
    NodeIdentity m_other;
};

// The request of the peer's requests whose method and target are line.
std::optional<Request> request_named(const StandInNode& node, const std::string& line) {
    const auto sent = node.requests();
    const auto found = std::find_if(sent.begin(), sent.end(), [&line](const Request& request) {
        return line_of(request) == line;
    });

    if (sent.end() == found) {
        return std::nullopt;
    }

    return *found;
}

} // namespace

// NOLINTBEGIN(bugprone-unchecked-optional-access)

TEST_F(FirstContactTest, KeyNotHeldIsFetchedAndKept) {
    Channel stored(DataStored::name);
    const StandInNode node(peer_answers());
    const auto contact = contact_at(node.address());
    const auto posted = stored.next<DataStored>(0ms);

    EXPECT_EQ(node.asked(), (std::vector<std::string>{put_line(), key_line(), "POST /data/nodes",
                                                      "GET /data/seek", "GET /data/nodes"}));
    EXPECT_EQ(contact.session.node_id().to_string(), peer().id().to_string());
    EXPECT_TRUE(contact.session.is_open());
    EXPECT_EQ(store()->get(peer().id()), Converter::bytes_of(peer().public_pem()));
    ASSERT_TRUE(posted.has_value());
    EXPECT_EQ(posted->identifier.to_string(), peer().id().to_string());
    ASSERT_TRUE(posted->node.has_value());
    EXPECT_EQ(posted->node->to_string(), peer().id().to_string());
    EXPECT_EQ(posted->size, peer().public_pem().size());
}

TEST_F(FirstContactTest, KeyHeldIsNotFetched) {
    hold_peer_key();

    Channel stored(DataStored::name);
    const StandInNode node(peer_answers());
    const auto contact = contact_at(node.address());

    EXPECT_EQ(node.asked(), (std::vector<std::string>{put_line(), "POST /data/nodes",
                                                      "GET /data/seek", "GET /data/nodes"}));
    EXPECT_EQ(contact.session.node_id().to_string(), peer().id().to_string());
    EXPECT_TRUE(contact.session.is_open());
    EXPECT_FALSE(stored.next<DataStored>(0ms).has_value());
}

// Whichever node answers is the node first contact is made with: which node
// was meant is for the one dialing to know.
TEST_F(FirstContactTest, SessionIsWithTheNodeThatAnswered) {
    auto answers = peer_answers();

    for (auto& [line, given] : answers) {
        given = signed_by(other_signer(), given);
    }

    const StandInNode node(std::move(answers));
    const auto contact = contact_at(node.address());

    EXPECT_EQ(contact.session.node_id().to_string(), other().id().to_string());
    EXPECT_EQ(node.asked().size(), 4U);
}

TEST_F(FirstContactTest, ThisNodesKeyIsPutSigned) {
    const StandInNode node(peer_answers());
    const auto contact = contact_at(node.address());
    const auto put = request_named(node, put_line());

    ASSERT_TRUE(put.has_value());
    EXPECT_EQ(put->body(), this_node().public_pem());
    EXPECT_EQ(put->headers().get("Content-Type"), "application/octet-stream");

    const auto signer = verifier().verify(*put);

    ASSERT_TRUE(signer.has_value());
    EXPECT_TRUE(signer->verified);
    EXPECT_EQ(signer->node_id.to_string(), this_node().id().to_string());
}

// This node's key is put as the store keeps it, which its id names, rather
// than in whatever form the key gives itself in now.
TEST_F(FirstContactTest, ThisNodesKeyIsPutAsStored) {
    const auto compressed = Converter::text_of(
        Zlib::compress(Converter::bytes_of(this_node().public_pem()), Zlib::balance_level));

    store()->put(this_node().id(), Converter::bytes_of(compressed));

    const StandInNode node(peer_answers());
    const auto contact = contact_at(node.address());
    const auto put = request_named(node, put_line());

    ASSERT_TRUE(put.has_value());
    EXPECT_EQ(put->body(), compressed);
    EXPECT_TRUE(contact.session.is_open());
}

TEST_F(FirstContactTest, ThisNodesKeyNotStoredIsPutAsGiven) {
    ASSERT_TRUE(store()->remove(this_node().id()));

    const StandInNode node(peer_answers());
    const auto contact = contact_at(node.address());
    const auto put = request_named(node, put_line());

    ASSERT_TRUE(put.has_value());
    EXPECT_EQ(put->body(), this_node().public_pem());
}

// What the store keeps under this node's id that is not its key is not put.
TEST_F(FirstContactTest, ThisNodesKeyStoredWrongIsPutAsGiven) {
    store()->put(this_node().id(), Converter::bytes_of("not the key"));

    const StandInNode node(peer_answers());
    const auto contact = contact_at(node.address());
    const auto put = request_named(node, put_line());

    ASSERT_TRUE(put.has_value());
    EXPECT_EQ(put->body(), this_node().public_pem());
}

TEST_F(FirstContactTest, ThisNodesListIsPosted) {
    const StandInNode node(peer_answers());
    const auto contact = contact_at(node.address());
    const auto posted = request_named(node, "POST /data/nodes");

    ASSERT_TRUE(posted.has_value());
    EXPECT_EQ(posted->headers().get("Content-Type"), "application/json");
    EXPECT_EQ(NodeList::parse(posted->body()).to_string(), own_list().to_string());
}

TEST_F(FirstContactTest, PeersSeekListIsGiven) {
    const StandInNode node(peer_answers());
    const auto contact = contact_at(node.address());

    ASSERT_EQ(contact.seek.data().size(), 1U);
    EXPECT_EQ(contact.seek.data().at(0).to_string(), peer_seek().data().at(0).to_string());
    ASSERT_EQ(contact.seek.search().size(), 1U);
    EXPECT_EQ(contact.seek.search().at(0).to_string(), "abcdef");
}

// The peer named itself at localhost, which is the host dialed, and at a
// loopback address, which it is kept at, as this node dialed one; the other
// node, which it relayed; and this node, which is left out.
TEST_F(FirstContactTest, PeersNodeListIsPosted) {
    Channel received(NodesReceived::name);
    const StandInNode node(peer_answers());
    const auto contact = contact_at(node.address());
    const auto posted = received.next<NodesReceived>(0ms);

    ASSERT_TRUE(posted.has_value());

    std::map<std::string, std::string> nodes;

    for (const auto& entry : posted->nodes.entries()) {
        nodes[entry.endpoint] = entry.node_id.to_string();
    }

    EXPECT_EQ(nodes, (std::map<std::string, std::string>{
                         {peer_resolved, peer().id().to_string()},
                         {peer_loopback, peer().id().to_string()},
                         {other_endpoint, other().id().to_string()},
                     }));
    EXPECT_EQ(posted->sources, (std::map<std::string, Source>{
                                   {peer_resolved, Source::Observed},
                                   {peer_loopback, Source::Advertised},
                               }));
}

// Step 6: what the peer seeks that this node holds is put, once the lists
// have been swapped.
TEST_F(FirstContactTest, HeldContentThePeerSeeksIsPut) {
    hold_peer_key();

    const Identifier hello("sha256", fixtures::hello_hash);
    auto answers = peer_answers();

    store()->put(hello, fixtures::bytes_of(fixtures::hello));
    answers["PUT " + data_path(hello)] = signed_by(peer_signer(), answer(Status::Created));

    const StandInNode node(std::move(answers));
    const auto contact = contact_at(node.address());
    const auto put = request_named(node, "PUT " + data_path(hello));

    EXPECT_EQ(node.asked(),
              (std::vector<std::string>{put_line(), "POST /data/nodes", "GET /data/seek",
                                        "GET /data/nodes", "PUT " + data_path(hello)}));
    ASSERT_TRUE(put.has_value());
    EXPECT_EQ(put->body(), fixtures::hello);
    EXPECT_TRUE(contact.session.is_open());
}

// Step 7: what this node seeks and does not hold is asked for, and what the
// peer sends that is the content is kept, as coming from the peer.
TEST_F(FirstContactTest, ContentThisNodeSeeksIsAskedForAndKept) {
    hold_peer_key();

    const auto world = Identifier::from(fixtures::bytes_of("world"));
    auto answers = peer_answers();

    answers["GET " + data_path(world)] = signed_by(peer_signer(), answer(Status::Ok, "world"));

    Channel stored(DataStored::name);
    const StandInNode node(std::move(answers));
    const auto contact = contact_at(node.address(), SeekList({world}, {}));
    const auto posted = stored.next<DataStored>(0ms);

    EXPECT_EQ(node.asked().back(), "GET " + data_path(world));
    EXPECT_EQ(store()->get(world), fixtures::bytes_of("world"));
    ASSERT_TRUE(posted.has_value());
    EXPECT_EQ(posted->identifier.to_string(), world.to_string());
    ASSERT_TRUE(posted->node.has_value());
    EXPECT_EQ(posted->node->to_string(), peer().id().to_string());
    EXPECT_EQ(posted->size, 5U);
    EXPECT_TRUE(contact.session.is_open());
}

// NOLINTEND(bugprone-unchecked-optional-access)

TEST_F(FirstContactTest, ContentThisNodeHoldsIsNotAskedFor) {
    hold_peer_key();

    const auto world = Identifier::from(fixtures::bytes_of("world"));

    store()->put(world, fixtures::bytes_of("world"));

    const StandInNode node(peer_answers());
    const auto contact = contact_at(node.address(), SeekList({world}, {}));

    EXPECT_EQ(node.asked(), (std::vector<std::string>{put_line(), "POST /data/nodes",
                                                      "GET /data/seek", "GET /data/nodes"}));
}

// The peer sends one thing that is not what was asked for, and does not
// send the other while it looks for it; neither is kept.
TEST_F(FirstContactTest, ContentNotSentIsNotKept) {
    hold_peer_key();

    const auto world = Identifier::from(fixtures::bytes_of("world"));
    const auto there = Identifier::from(fixtures::bytes_of("there"));
    auto answers = peer_answers();

    answers["GET " + data_path(world)] = signed_by(peer_signer(), answer(Status::Ok, "jello"));
    answers["GET " + data_path(there)] =
        signed_by(peer_signer(), answer(Status::ServiceUnavailable));

    Channel stored(DataStored::name);
    const StandInNode node(std::move(answers));
    const auto contact = contact_at(node.address(), SeekList({world, there}, {}));

    EXPECT_EQ(node.asked().size(), 6U);
    EXPECT_FALSE(store()->has(world));
    EXPECT_FALSE(store()->has(there));
    EXPECT_FALSE(stored.next<DataStored>(0ms).has_value());
    EXPECT_TRUE(contact.session.is_open());
}

// A peer may turn this node away in any answer, here the first of two
// puts. It is asked for nothing more, and the one dialing it is told.
TEST_F(FirstContactTest, PeerTurningThisNodeAwayWhileFulfilledIsTold) {
    hold_peer_key();

    const Identifier hello("sha256", fixtures::hello_hash);
    const auto world = Identifier::from(fixtures::bytes_of("world"));
    const auto there = Identifier::from(fixtures::bytes_of("there"));
    const SeekList sought({hello, world}, {});
    auto answers = peer_answers();
    auto closing = answer(Status::Created);

    store()->put(hello, fixtures::bytes_of(fixtures::hello));
    store()->put(world, fixtures::bytes_of("world"));
    closing.headers().set("Connection", "close");
    answers["GET /data/seek"] =
        signed_by(peer_signer(), answer(Status::Ok, Converter::text_of(sought.write())));
    answers["PUT " + data_path(hello)] = signed_by(peer_signer(), closing);
    answers["PUT " + data_path(world)] = signed_by(peer_signer(), answer(Status::Created));
    answers["GET " + data_path(there)] = signed_by(peer_signer(), answer(Status::Ok, "there"));

    const StandInNode node(std::move(answers));
    const auto contact = contact_at(node.address(), SeekList({there}, {}));
    const auto asked = node.asked();

    EXPECT_TRUE(contact.session.was_turned_away());
    EXPECT_FALSE(contact.session.is_open());
    EXPECT_EQ(std::count(asked.begin(), asked.end(), "GET " + data_path(there)), 0);
    EXPECT_FALSE(store()->has(there));
}

TEST_F(FirstContactTest, ListNamingOnlyThisNodeIsNotPosted) {
    auto answers = peer_answers();
    const NodeList only_this_node(this_node().id(), {own_endpoint});

    answers["GET /data/nodes"] =
        signed_by(peer_signer(), answer(Status::Ok, only_this_node.to_string()));

    Channel received(NodesReceived::name);
    const StandInNode node(std::move(answers));
    const auto contact = contact_at(node.address());

    EXPECT_FALSE(received.next<NodesReceived>(0ms).has_value());
}

TEST_F(FirstContactTest, ListsThatCannotBeReadAreNone) {
    auto answers = peer_answers();

    answers["GET /data/seek"] = signed_by(peer_signer(), answer(Status::InternalServerError));
    answers["GET /data/nodes"] = signed_by(peer_signer(), answer(Status::Ok, "not a list"));

    Channel received(NodesReceived::name);
    const StandInNode node(std::move(answers));
    const auto contact = contact_at(node.address());

    EXPECT_TRUE(contact.seek.data().empty());
    EXPECT_TRUE(contact.seek.search().empty());
    EXPECT_FALSE(received.next<NodesReceived>(0ms).has_value());
    EXPECT_TRUE(contact.session.is_open());
}

// A server that will not keep this node answers its list with Connection:
// close, and the one dialing it is told, to rest it.
TEST_F(FirstContactTest, PeerTurningThisNodeAwayIsTold) {
    auto answers = peer_answers();
    auto closing = answer(Status::Ok, peer_list().to_string());

    closing.headers().set("Connection", "close");
    answers["GET /data/nodes"] = signed_by(peer_signer(), closing);

    const StandInNode node(std::move(answers));
    const auto contact = contact_at(node.address());

    EXPECT_TRUE(contact.session.was_turned_away());
    EXPECT_FALSE(contact.session.is_open());
}

TEST_F(FirstContactTest, UnsignedFirstAnswerIsRefused) {
    auto answers = peer_answers();

    answers[put_line()] = answer(Status::Created);

    const StandInNode node(std::move(answers));

    EXPECT_THROW((void)contact_at(node.address()), PeerSession::Unauthenticated);
    EXPECT_EQ(node.asked(), std::vector<std::string>{put_line()});
}

TEST_F(FirstContactTest, FirstAnswerWhoseSignatureFailsIsRefused) {
    auto answers = peer_answers();
    auto changed = signed_by(peer_signer(), answer(Status::Created, "hello"));

    changed.set_body("jello");
    answers[put_line()] = changed;

    const StandInNode node(std::move(answers));

    EXPECT_THROW((void)contact_at(node.address()), PeerSession::Unauthenticated);
    EXPECT_EQ(node.asked(), std::vector<std::string>{put_line()});
}

TEST_F(FirstContactTest, KeyNotSentIsRefused) {
    auto answers = peer_answers();

    answers[key_line()] = signed_by(peer_signer(), answer(Status::NotFound));

    const StandInNode node(std::move(answers));

    EXPECT_THROW((void)contact_at(node.address()), PeerSession::Unauthenticated);
    EXPECT_FALSE(holds_peer_key());
}

TEST_F(FirstContactTest, KeyThatIsNotThePeersIsRefused) {
    auto answers = peer_answers();

    answers[key_line()] = signed_by(peer_signer(), answer(Status::Ok, other().public_pem()));

    Channel stored(DataStored::name);
    const StandInNode node(std::move(answers));

    EXPECT_THROW((void)contact_at(node.address()), PeerSession::Unauthenticated);
    EXPECT_FALSE(holds_peer_key());
    EXPECT_FALSE(stored.next<DataStored>(0ms).has_value());
}

// The peer's key came, but signed by the other node, so the peer has not
// shown it holds it, and it is not kept.
TEST_F(FirstContactTest, KeyBroughtUnderAnotherSignatureIsRefused) {
    auto answers = peer_answers();

    answers[key_line()] = signed_by(other_signer(), answer(Status::Ok, peer().public_pem()));

    Channel stored(DataStored::name);
    const StandInNode node(std::move(answers));

    EXPECT_THROW((void)contact_at(node.address()), PeerSession::Unauthenticated);
    EXPECT_FALSE(holds_peer_key());
    EXPECT_FALSE(stored.next<DataStored>(0ms).has_value());
}

TEST_F(FirstContactTest, NeedsAStoreAFulfillerAndAConnection) {
    EXPECT_THROW((void)FirstContact(this_node(), nullptr, verifier(), fulfiller()),
                 std::invalid_argument);
    EXPECT_THROW((void)FirstContact(this_node(), store(), verifier(), nullptr),
                 std::invalid_argument);

    const FirstContact first_contact(this_node(), store(), verifier(), fulfiller());

    EXPECT_THROW((void)first_contact.make(nullptr, own_list(), {}), std::invalid_argument);
}
