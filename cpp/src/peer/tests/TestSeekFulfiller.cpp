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
#include "peer/PeerConnection.hpp"
#include "peer/PeerSession.hpp"
#include "peer/SeekFulfiller.hpp"
#include "protocol/SearchResults.hpp"
#include "protocol/SeekList.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"
#include "storage/tests/Fixtures.hpp"
#include "types/Converter.hpp"

using http::Request;
using http::Response;
using identity::KnownKeys;
using identity::MessageSigner;
using identity::MessageVerifier;
using identity::NodeIdentity;
using logger::Logger;
using net::Address;
using net::ISocket;
using peer::PeerConnection;
using peer::PeerSession;
using peer::SeekFulfiller;
using protocol::SearchResults;
using protocol::SeekList;
using storage::HashPrefix;
using storage::Identifier;
using types::Converter;
using namespace std::chrono_literals;
using Status = http::ResponseCode::Status;

namespace {

// How long a node waits, at most, for this node to send something, so that
// a test fails rather than waits forever when it does not come.
constexpr auto patience = std::chrono::seconds(10);

// The header that gives a body's length, as a line of a message's head.
constexpr std::string_view length_line = "Content-Length: ";

// How large a list or an answer the tests read may be once decompressed.
constexpr std::size_t decompressed_limit = 4'194'304;

// The prefix the peers search for, and the leading digits of a hash that
// matches it and of one that does not. No key the tests make is likely to
// match it: one in 2^24 shares as many bits.
constexpr auto searched = "abcdef";
constexpr auto matching_digits = "abcdef";
constexpr auto other_digits = "123456";

// The next request on socket, as sent: its line and headers, each ended with
// a CRLF, and its body, as long as its Content-Length gives. Nothing once
// the connection has closed.
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

// An answer of status with the body, and its length.
Response answer(Status status, std::string body = "") {
    Response made;

    made.set_status_code(status);
    made.headers().set("Content-Length", std::to_string(body.size()));
    made.set_body(std::move(body));
    return made;
}

// The request's method and target.
std::string line_of(const Request& request) {
    return std::string(request.method()) + " " + std::string(request.uri());
}

// The id of content whose hash begins with digits and ends with number,
// with zeros between. The tests' store need not hold the content a hash is
// of.
Identifier id_of(std::string_view digits, int number) {
    constexpr std::size_t hash_digits = 64;
    const auto last = std::to_string(number);
    const std::string zeros(hash_digits - digits.size() - last.size(), '0');

    return {"sha256", std::string(digits) + zeros + last};
}

// The identifiers named in the body of a post naming what matches a search.
std::vector<std::string> named_in(const Request& post) {
    const auto results = SearchResults::parse(post.body(), decompressed_limit).results();
    std::vector<std::string> named;

    std::transform(results.begin(), results.end(), std::back_inserter(named),
                   [](const Identifier& identifier) { return identifier.to_string(); });
    return named;
}

// A node on the loopback address that takes one connection, and answers each
// request on it, on a thread of its own, until the connection closes: a get
// of its seek list with the list it seeks then, a put with 201 Created, a
// post with 202 Accepted, and any other with 404 Not Found, each signed by
// signer, unless there is none. It keeps each request it is sent.
class StandInNode {
public:
    explicit StandInNode(std::optional<MessageSigner> signer);
    ~StandInNode();
    StandInNode(const StandInNode&) = delete;
    StandInNode& operator=(const StandInNode&) = delete;
    StandInNode(StandInNode&&) = delete;
    StandInNode& operator=(StandInNode&&) = delete;

    [[nodiscard]] Address address() const;
    // It seeks what list names, from now.
    void seeks(const SeekList& list);
    // It answers a get of its seek list with status, and no list, from now.
    void answers_seek_with(Status status);
    // The method and target of each request it has been sent, in order.
    [[nodiscard]] std::vector<std::string> asked() const;
    // The requests it has been sent, in order.
    [[nodiscard]] std::vector<Request> requests() const;
    // Those it has been sent of method.
    [[nodiscard]] std::vector<Request> requests_of(std::string_view method) const;

private:
    ISocket::Ptr m_listener;
    std::optional<MessageSigner> m_signer;
    mutable std::mutex m_mutex;
    Response m_seek_answer;          // Guarded by m_mutex
    std::vector<Request> m_requests; // Guarded by m_mutex
    std::thread m_thread;

    [[nodiscard]] Response _answer_to(Request request);
};

StandInNode::StandInNode(std::optional<MessageSigner> signer)
    : m_listener(ISocket::bind(Address("127.0.0.1", 0)))
    , m_signer(std::move(signer))
    , m_seek_answer(answer(Status::Ok, Converter::text_of(SeekList().write()))) {
    m_listener->listen(1);
    m_thread = std::thread([this] {
        try {
            const auto connection = m_listener->accept();

            connection->set_receive_timeout(patience);

            while (auto request = read_request(*connection)) {
                connection->send_all(_answer_to(std::move(*request)).to_string());
            }
            // This node may end the connection while answers are on their
            // way, as a session does once one fails its check.
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

void StandInNode::seeks(const SeekList& list) {
    const std::scoped_lock lock(m_mutex);

    m_seek_answer = answer(Status::Ok, Converter::text_of(list.write()));
}

void StandInNode::answers_seek_with(Status status) {
    const std::scoped_lock lock(m_mutex);

    m_seek_answer = answer(status);
}

std::vector<std::string> StandInNode::asked() const {
    const auto sent = requests();
    std::vector<std::string> lines;

    std::transform(sent.begin(), sent.end(), std::back_inserter(lines), &line_of);
    return lines;
}

std::vector<Request> StandInNode::requests() const {
    const std::scoped_lock lock(m_mutex);

    return m_requests;
}

std::vector<Request> StandInNode::requests_of(std::string_view method) const {
    const auto sent = requests();
    std::vector<Request> found;

    std::copy_if(sent.begin(), sent.end(), std::back_inserter(found),
                 [method](const Request& request) { return request.method() == method; });
    return found;
}

// The answer to request, which is kept, signed unless there is no signer.
Response StandInNode::_answer_to(Request request) {
    const std::scoped_lock lock(m_mutex);
    const auto& method = request.method();
    auto given = answer(Status::NotFound);

    if ("GET /data/seek" == line_of(request)) {
        given = m_seek_answer;
    } else if ("PUT" == method) {
        given = answer(Status::Created);
    } else if ("POST" == method) {
        given = answer(Status::Accepted);
    }

    if (m_signer) {
        m_signer->sign(given);
    }

    m_requests.push_back(std::move(request));
    return given;
}

// Each test has this node, and two peers it may have sessions with, each
// with a key of its own, and a store that holds their keys and the content
// this node holds. Time passes only when a test says, and what is logged is
// kept.
class SeekFulfillerTest : public testing::Test {
protected:
    SeekFulfillerTest()
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

    // A fulfiller of what this node holds, with settings, at the tests' time.
    [[nodiscard]] std::shared_ptr<SeekFulfiller>
    fulfiller(const SeekFulfiller::Settings& settings = {}) const {
        return std::make_shared<SeekFulfiller>(m_store, settings, [this] { return m_now; });
    }

    // A session with the first peer, or the second, at address.
    [[nodiscard]] std::shared_ptr<PeerSession> first_at(const Address& address) const {
        return _session_with(m_first.id(), address);
    }

    [[nodiscard]] std::shared_ptr<PeerSession> second_at(const Address& address) const {
        return _session_with(m_second.id(), address);
    }

    [[nodiscard]] MessageSigner first_signer() const {
        return MessageSigner(m_first);
    }

    [[nodiscard]] MessageSigner second_signer() const {
        return MessageSigner(m_second);
    }

    // This node holds body, as the content identifier names.
    void hold(const Identifier& identifier, std::string_view body = "content") const {
        m_store->put(identifier, fixtures::bytes_of(body));
    }

    // Whether request was signed by this node, with its key.
    [[nodiscard]] bool signed_by_this_node(const Request& request) const {
        const auto signer = MessageVerifier(std::make_shared<KnownKeys>(m_store), {}).verify(request);

        return signer && signer->verified
               && signer->node_id.to_string() == m_this_node.id().to_string();
    }

    void advance(std::chrono::milliseconds elapsed) {
        m_now += elapsed;
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
    SeekFulfiller::Clock::time_point m_now;

    [[nodiscard]] std::shared_ptr<PeerSession> _session_with(const Identifier& node_id,
                                                             const Address& address) const {
        return std::make_shared<PeerSession>(
            std::make_unique<PeerConnection>(address, PeerConnection::Limits{}), node_id,
            MessageSigner(m_this_node), MessageVerifier(std::make_shared<KnownKeys>(m_store), {}));
    }
};

// The data entries of a seek list, and its search entries.
SeekList seeking(std::vector<Identifier> data, const std::vector<std::string>& search = {}) {
    std::vector<HashPrefix> prefixes;

    std::transform(search.begin(), search.end(), std::back_inserter(prefixes), &HashPrefix::parse);
    return {std::move(data), std::move(prefixes)};
}

} // namespace

TEST_F(SeekFulfillerTest, HeldDataEntriesArePutInTheirOrder) {
    const auto first = id_of(other_digits, 1);
    const auto lacked = id_of(other_digits, 2);
    const auto second = id_of(other_digits, 3);

    hold(first, "first");
    hold(second, "second");

    const StandInNode node(first_signer());
    const auto session = first_at(node.address());

    fulfiller()->fulfill(*session, seeking({second, lacked, first}));

    const auto puts = node.requests_of("PUT");

    ASSERT_EQ(puts.size(), 2U);
    EXPECT_EQ(puts.at(0).uri(), "/data/" + second.to_string());
    EXPECT_EQ(puts.at(0).body(), "second");
    EXPECT_EQ(puts.at(0).headers().get("Content-Type"), "application/octet-stream");
    EXPECT_TRUE(signed_by_this_node(puts.at(0)));
    EXPECT_EQ(puts.at(1).uri(), "/data/" + first.to_string());
    EXPECT_EQ(puts.at(1).body(), "first");
}

TEST_F(SeekFulfillerTest, EntryListedTwiceIsPutOnce) {
    const auto held = id_of(other_digits, 1);

    hold(held);

    const StandInNode node(first_signer());
    const auto session = first_at(node.address());

    fulfiller()->fulfill(*session, seeking({held, held}));

    EXPECT_EQ(node.requests_of("PUT").size(), 1U);
}

// Each run holds no more than one, and every one is put.
TEST_F(SeekFulfillerTest, EachHeldIsPutWhateverThePipelineDepth) {
    SeekFulfiller::Settings settings;
    std::vector<Identifier> held;

    settings.pipeline_depth = 1;

    for (int number = 0; number < 3; ++number) {
        held.push_back(id_of(other_digits, number));
        hold(held.back());
    }

    const StandInNode node(first_signer());
    const auto session = first_at(node.address());

    fulfiller(settings)->fulfill(*session, seeking(held));

    EXPECT_EQ(node.requests_of("PUT").size(), 3U);
}

TEST_F(SeekFulfillerTest, SearchEntryIsAnsweredWithWhatMatchesBestFirst) {
    const auto close = id_of("abcdef0", 1);
    const auto closer = id_of("abcdef1", 1);
    const auto far = id_of(other_digits, 1);

    hold(close);
    hold(closer);
    hold(far);

    const StandInNode node(first_signer());
    const auto session = first_at(node.address());

    fulfiller()->fulfill(*session, seeking({}, {"abcdef1"}));

    const auto posts = node.requests_of("POST");

    ASSERT_EQ(posts.size(), 1U);
    EXPECT_EQ(posts.at(0).uri(), "/data/search/abcdef1");
    EXPECT_EQ(posts.at(0).headers().get("Content-Type"), "application/json");
    EXPECT_TRUE(signed_by_this_node(posts.at(0)));
    EXPECT_EQ(named_in(posts.at(0)),
              (std::vector<std::string>{closer.to_string(), close.to_string()}));
}

TEST_F(SeekFulfillerTest, SearchEntryNothingMatchesIsPostedNothing) {
    hold(id_of(other_digits, 1));

    const StandInNode node(first_signer());
    const auto session = first_at(node.address());

    fulfiller()->fulfill(*session, seeking({}, {searched}));

    EXPECT_TRUE(node.asked().empty());
}

// What is named once is not named again while the entry stays listed;
// what this node comes to hold since is.
TEST_F(SeekFulfillerTest, EachMatchIsNamedOnceWhileTheEntryIsListed) {
    const auto earlier = id_of(matching_digits, 1);
    const auto later = id_of(matching_digits, 2);
    const auto list = seeking({}, {searched});
    const auto fulfilling = fulfiller();
    StandInNode node(first_signer());
    const auto session = first_at(node.address());

    hold(earlier);
    node.seeks(list);
    fulfilling->fulfill(*session, list);
    advance(SeekFulfiller::Settings::default_refresh);
    EXPECT_EQ(fulfilling->refresh({session}), 1U);
    hold(later);
    advance(SeekFulfiller::Settings::default_refresh);
    EXPECT_EQ(fulfilling->refresh({session}), 1U);

    const auto posts = node.requests_of("POST");

    ASSERT_EQ(posts.size(), 2U);
    EXPECT_EQ(named_in(posts.at(0)), std::vector<std::string>{earlier.to_string()});
    EXPECT_EQ(named_in(posts.at(1)), std::vector<std::string>{later.to_string()});
}

TEST_F(SeekFulfillerTest, EntryListedAgainIsNamedAgain) {
    const auto held = id_of(matching_digits, 1);
    const auto list = seeking({}, {searched});
    const auto fulfilling = fulfiller();
    StandInNode node(first_signer());
    const auto session = first_at(node.address());

    hold(held);
    fulfilling->fulfill(*session, list);
    node.seeks(SeekList());
    advance(SeekFulfiller::Settings::default_refresh);
    EXPECT_EQ(fulfilling->refresh({session}), 1U);
    node.seeks(list);
    advance(SeekFulfiller::Settings::default_refresh);
    EXPECT_EQ(fulfilling->refresh({session}), 1U);

    const auto posts = node.requests_of("POST");

    ASSERT_EQ(posts.size(), 2U);
    EXPECT_EQ(named_in(posts.at(1)), std::vector<std::string>{held.to_string()});
}

// The peer seeks what was just put to it by its id, and by a prefix it
// matches; it is not named, as it was put.
TEST_F(SeekFulfillerTest, WhatWasPushedIsNotNamed) {
    const auto pushed = id_of(matching_digits, 1);
    const auto other = id_of(matching_digits, 2);

    hold(pushed);
    hold(other);

    const StandInNode node(first_signer());
    const auto session = first_at(node.address());

    fulfiller()->fulfill(*session, seeking({pushed}, {searched}));

    const auto posts = node.requests_of("POST");

    EXPECT_EQ(node.requests_of("PUT").size(), 1U);
    ASSERT_EQ(posts.size(), 1U);
    EXPECT_EQ(named_in(posts.at(0)), std::vector<std::string>{other.to_string()});
}

// The peer still lists what was put to it, as it has not derived its list
// again yet; it is not put again on the same connection.
TEST_F(SeekFulfillerTest, WhatWasPushedIsNotPushedAgain) {
    const auto held = id_of(other_digits, 1);
    const auto list = seeking({held});
    const auto fulfilling = fulfiller();
    StandInNode node(first_signer());
    const auto session = first_at(node.address());

    hold(held);
    node.seeks(list);
    fulfilling->fulfill(*session, list);
    advance(SeekFulfiller::Settings::default_refresh);
    EXPECT_EQ(fulfilling->refresh({session}), 1U);
    EXPECT_EQ(node.asked(), (std::vector<std::string>{"PUT /data/" + held.to_string(),
                                                      "GET /data/seek"}));
}

// A new connection to the node is a new first contact, and what was put and
// named on the one before is put and named again.
TEST_F(SeekFulfillerTest, NewConnectionStartsAfresh) {
    const auto held = id_of(matching_digits, 1);
    const auto list = seeking({held}, {searched});
    const auto fulfilling = fulfiller();
    const StandInNode before(first_signer());
    const StandInNode after(first_signer());

    hold(held);
    fulfilling->fulfill(*first_at(before.address()), list);
    fulfilling->fulfill(*first_at(after.address()), list);

    EXPECT_EQ(after.asked(), before.asked());
    EXPECT_EQ(after.requests_of("PUT").size(), 1U);
}

TEST_F(SeekFulfillerTest, ListIsReadAgainOnlyOnceDue) {
    const auto fulfilling = fulfiller();
    const StandInNode node(first_signer());
    const auto session = first_at(node.address());

    fulfilling->fulfill(*session, SeekList());
    advance(SeekFulfiller::Settings::default_refresh - 1ms);
    EXPECT_EQ(fulfilling->refresh({session}), 0U);
    EXPECT_TRUE(node.asked().empty());
    advance(1ms);
    EXPECT_EQ(fulfilling->refresh({session}), 1U);
    EXPECT_EQ(fulfilling->refresh({session}), 0U);
    EXPECT_EQ(node.asked(), std::vector<std::string>{"GET /data/seek"});
}

// A session whose list was not read at first contact is read at once.
TEST_F(SeekFulfillerTest, ListNeverReadIsReadAtOnce) {
    const auto held = id_of(other_digits, 1);
    StandInNode node(first_signer());
    const auto session = first_at(node.address());

    hold(held);
    node.seeks(seeking({held}));

    EXPECT_EQ(fulfiller()->refresh({session}), 1U);
    EXPECT_EQ(node.asked(), (std::vector<std::string>{"GET /data/seek",
                                                      "PUT /data/" + held.to_string()}));
}

TEST_F(SeekFulfillerTest, EachPeersListIsRead) {
    const auto first_held = id_of(other_digits, 1);
    const auto second_held = id_of(other_digits, 2);
    StandInNode first(first_signer());
    StandInNode second(second_signer());

    hold(first_held);
    hold(second_held);
    first.seeks(seeking({first_held}));
    second.seeks(seeking({second_held}));

    EXPECT_EQ(fulfiller()->refresh({first_at(first.address()), second_at(second.address())}), 2U);
    EXPECT_EQ(first.requests_of("PUT").size(), 1U);
    EXPECT_EQ(first.requests_of("PUT").at(0).uri(), "/data/" + first_held.to_string());
    EXPECT_EQ(second.requests_of("PUT").size(), 1U);
    EXPECT_EQ(second.requests_of("PUT").at(0).uri(), "/data/" + second_held.to_string());
}

// Once its list is due, what was put to a node no session is open to is let
// go, as its connection has ended, and it is put again on the next.
TEST_F(SeekFulfillerTest, NodeNoLongerConnectedIsLetGo) {
    const auto held = id_of(other_digits, 1);
    const auto list = seeking({held});
    const auto fulfilling = fulfiller();
    StandInNode node(first_signer());
    const auto session = first_at(node.address());

    hold(held);
    node.seeks(list);
    fulfilling->fulfill(*session, list);
    advance(SeekFulfiller::Settings::default_refresh);
    EXPECT_EQ(fulfilling->refresh({}), 0U);
    EXPECT_EQ(fulfilling->refresh({session}), 1U);
    EXPECT_EQ(node.requests_of("PUT").size(), 2U);
}

TEST_F(SeekFulfillerTest, SessionsClosedOrMissingAreNotRead) {
    const StandInNode node(first_signer());
    const auto closed = first_at(node.address());

    closed->close();

    EXPECT_EQ(fulfiller()->refresh({nullptr, closed}), 0U);
    EXPECT_TRUE(node.asked().empty());
}

// The peer answers without its list for now; nothing is put or named, and
// what was named is still named.
TEST_F(SeekFulfillerTest, ListNotSentIsNotCounted) {
    const auto held = id_of(matching_digits, 1);
    const auto list = seeking({}, {searched});
    const auto fulfilling = fulfiller();
    StandInNode node(first_signer());
    const auto session = first_at(node.address());

    hold(held);
    fulfilling->fulfill(*session, list);
    node.answers_seek_with(Status::ServiceUnavailable);
    advance(SeekFulfiller::Settings::default_refresh);
    EXPECT_EQ(fulfilling->refresh({session}), 0U);
    node.seeks(list);
    advance(SeekFulfiller::Settings::default_refresh);
    EXPECT_EQ(fulfilling->refresh({session}), 1U);
    EXPECT_EQ(node.requests_of("POST").size(), 1U);
    EXPECT_TRUE(session->is_open());
}

TEST_F(SeekFulfillerTest, ListThatCannotBeReadIsNotCounted) {
    StandInNode node(first_signer());
    const auto session = first_at(node.address());

    node.answers_seek_with(Status::Ok);

    EXPECT_EQ(fulfiller()->refresh({session}), 0U);
    EXPECT_EQ(node.asked(), std::vector<std::string>{"GET /data/seek"});
    EXPECT_TRUE(session->is_open());
}

// The answer is not signed, so the session ends, and the read is logged as
// having failed.
TEST_F(SeekFulfillerTest, ReadThatFailsIsLoggedAndNotCounted) {
    const StandInNode node(std::nullopt);
    const auto session = first_at(node.address());

    EXPECT_EQ(fulfiller()->refresh({session}), 0U);
    EXPECT_TRUE(logged("Could not fulfill the seek list of"));
    EXPECT_FALSE(session->is_open());
}

TEST_F(SeekFulfillerTest, FulfillThrowsWhatTheSessionThrows) {
    const auto held = id_of(other_digits, 1);
    const StandInNode node(std::nullopt);
    const auto session = first_at(node.address());

    hold(held);

    EXPECT_THROW(fulfiller()->fulfill(*session, seeking({held})), PeerSession::Unauthenticated);
}

TEST_F(SeekFulfillerTest, PipelineDepthIsGiven) {
    SeekFulfiller::Settings settings;

    settings.pipeline_depth = 3;

    EXPECT_EQ(fulfiller(settings)->pipeline_depth(), 3U);
}

TEST_F(SeekFulfillerTest, SettingsThatCannotBeWorkedWithAreRefused) {
    SeekFulfiller::Settings no_refresh;
    SeekFulfiller::Settings no_depth;

    no_refresh.refresh = 0ms;
    no_depth.pipeline_depth = 0;

    EXPECT_THROW((void)fulfiller(no_refresh), std::invalid_argument);
    EXPECT_THROW((void)fulfiller(no_depth), std::invalid_argument);
}

TEST_F(SeekFulfillerTest, NeedsAStore) {
    EXPECT_THROW((void)SeekFulfiller(nullptr, {}), std::invalid_argument);
}

TEST_F(SeekFulfillerTest, NeedsTheTime) {
    EXPECT_THROW((void)SeekFulfiller(std::make_shared<fixtures::MemorySource>(), {}, nullptr),
                 std::invalid_argument);
}
