#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <cstddef>
#include <exception>
#include <filesystem>
#include <functional>
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
#include "msg/Channel.hpp"
#include "msg/Message.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "peer/PeerConnection.hpp"
#include "peer/PeerSearch.hpp"
#include "peer/PeerSession.hpp"
#include "protocol/SearchResults.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "storage/SearchAnswered.hpp"
#include "storage/tests/Fixtures.hpp"
#include "types/Converter.hpp"

using http::Request;
using http::Response;
using http::ResponseCode;
using identity::KnownKeys;
using identity::MessageSigner;
using identity::MessageVerifier;
using identity::NodeIdentity;
using msg::Channel;
using net::Address;
using net::ISocket;
using peer::PeerConnection;
using peer::PeerSearch;
using peer::PeerSession;
using protocol::SearchResults;
using storage::HashPrefix;
using storage::IDataSource;
using storage::Identifier;
using storage::SearchAnswered;
using types::Converter;

// The tests only take what the search posts, from another file, so nothing
// here would make its messages' virtual to_string(), which next<>() casts
// to.
template class msg::Message<SearchAnswered>;

namespace {

using Clock = PeerSearch::Clock;
using Milliseconds = PeerSearch::Milliseconds;

// How long a peer waits, at most, for this node to connect or send
// something, so that a test fails rather than waits forever when it does
// not come.
constexpr auto patience = std::chrono::seconds(10);

// The header that gives a body's length, as a line of a message's head.
constexpr std::string_view length_line = "Content-Length: ";

// How a peer answers a request: with the text this gives, which it may sign
// with signer, the peer's.
using Answering = std::function<std::string(const Request& request, const MessageSigner& signer)>;

// A request a peer was sent: the peer's id, the request's method and
// target, and the X-Search-Relays it gave, if any.
struct Asked {
    std::string node_id;
    std::string request;
    std::string relays;
};

// The requests the peers were sent, in the order they came, noted from the
// peers' threads.
class Requests {
public:
    void add(const Identifier& node_id, const Request& request);
    // Those noted since this was last called, in order.
    [[nodiscard]] std::vector<Asked> taken();

private:
    std::mutex m_mutex;
    std::vector<Asked> m_asked;
};

void Requests::add(const Identifier& node_id, const Request& request) {
    const std::scoped_lock lock(m_mutex);

    m_asked.push_back({
        node_id.to_string(),
        std::string(request.method()) + " " + std::string(request.uri()),
        std::string(request.headers().get("X-Search-Relays", "none")),
    });
}

std::vector<Asked> Requests::taken() {
    const std::scoped_lock lock(m_mutex);

    return std::exchange(m_asked, {});
}

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

// A peer on the loopback address, with a key of its own, which the store it
// is made with holds. It takes one connection, notes each request on it in
// the requests it is given, and answers it as answering says, on a thread of
// its own, until the connection closes.
class StandInPeer {
public:
    StandInPeer(const std::filesystem::path& key_file, const IDataSource& store,
                Answering answering, Requests& asked);
    ~StandInPeer();
    StandInPeer(const StandInPeer&) = delete;
    StandInPeer& operator=(const StandInPeer&) = delete;
    StandInPeer(StandInPeer&&) = delete;
    StandInPeer& operator=(StandInPeer&&) = delete;

    [[nodiscard]] const Identifier& id() const;
    [[nodiscard]] Address address() const;

private:
    NodeIdentity m_identity;
    ISocket::Ptr m_listener;
    std::thread m_thread;

    void _serve(const Answering& answering, Requests& asked);
};

StandInPeer::StandInPeer(const std::filesystem::path& key_file, const IDataSource& store,
                         Answering answering, Requests& asked)
    : m_identity(key_file, store)
    , m_listener(ISocket::bind(Address("127.0.0.1", 0))) {
    m_listener->listen(1);
    m_listener->set_receive_timeout(patience);
    m_thread = std::thread([this, answering = std::move(answering), &asked] {
        _serve(answering, asked);
    });
}

StandInPeer::~StandInPeer() {
    m_thread.join();
}

const Identifier& StandInPeer::id() const {
    return m_identity.id();
}

Address StandInPeer::address() const {
    return m_listener->local_address();
}

void StandInPeer::_serve(const Answering& answering, Requests& asked) {
    const MessageSigner signer(m_identity);

    try {
        const auto connection = m_listener->accept();

        connection->set_receive_timeout(patience);

        while (const auto text = read_request(*connection)) {
            std::istringstream stream(*text);
            Request request;

            stream >> request;
            asked.add(id(), request);
            connection->send_all(answering(request, signer));
        }
        // This node may end the connection while an answer is on its way, as
        // a session does once one fails its check.
        // NOLINTNEXTLINE(bugprone-empty-catch)
    } catch (const std::system_error&) {
    } catch (const std::exception& error) {
        ADD_FAILURE() << "The peer failed to answer: " << error.what();
    }
}

// response, with the length of its body, as sent; signed by signer, unless
// there is none.
std::string sent(Response response, const MessageSigner* signer) {
    response.headers().set("Content-Length", std::to_string(response.body().size()));

    if (nullptr != signer) {
        signer->sign(response);
    }

    return response.to_string();
}

// Answers each search with results, in an answer it signs.
Answering finding(std::vector<Identifier> results) {
    return [results = std::move(results)](const Request& /*request*/,
                                          const MessageSigner& signer) {
        Response response;

        SearchResults(results).answer(response);
        return sent(std::move(response), &signer);
    };
}

// Answers each request with status, and no body, in an answer it signs.
Answering answering_with(ResponseCode::Status status) {
    return [status](const Request& /*request*/, const MessageSigner& signer) {
        Response response;

        response.set_status_code(status);
        return sent(std::move(response), &signer);
    };
}

// Answers each request with a body that is not a search's answer, in an
// answer it signs.
std::string not_a_search_answer(const Request& /*request*/, const MessageSigner& signer) {
    Response response;

    response.set_body("not a search answer");
    return sent(std::move(response), &signer);
}

// Answers each request with what is not HTTP at all.
std::string not_http(const Request& /*request*/, const MessageSigner& /*signer*/) {
    constexpr std::string_view text = "This is not HTTP\r\n\r\n";

    return std::string(text);
}

// Answers each search with an answer naming nothing, unsigned.
std::string unsigned_answer(const Request& /*request*/, const MessageSigner& /*signer*/) {
    Response response;

    SearchResults({}).answer(response);
    return sent(std::move(response), nullptr);
}

// An identifier whose hash is 64 of digit.
Identifier identifier_of(char digit) {
    constexpr std::size_t sha256_digits = 64;

    return {"sha256", std::string(sha256_digits, digit)};
}

// The text of each of identifiers, in order.
std::vector<std::string> texts_of(const std::vector<Identifier>& identifiers) {
    std::vector<std::string> texts;

    std::transform(identifiers.begin(), identifiers.end(), std::back_inserter(texts),
                   [](const Identifier& identifier) { return identifier.to_string(); });
    return texts;
}

// The method and target of each of asked, in order.
std::vector<std::string> requests_of(const std::vector<Asked>& asked) {
    std::vector<std::string> requests;

    std::transform(asked.begin(), asked.end(), std::back_inserter(requests),
                   [](const Asked& one) { return one.request; });
    return requests;
}

// The node id each of asked was sent to, in order.
std::vector<std::string> node_ids_of(const std::vector<Asked>& asked) {
    std::vector<std::string> node_ids;

    std::transform(asked.begin(), asked.end(), std::back_inserter(node_ids),
                   [](const Asked& one) { return one.node_id; });
    return node_ids;
}

// The X-Search-Relays each of asked gave, in order.
std::vector<std::string> relays_of(const std::vector<Asked>& asked) {
    std::vector<std::string> relays;

    std::transform(asked.begin(), asked.end(), std::back_inserter(relays),
                   [](const Asked& one) { return one.relays; });
    return relays;
}

// The ids of peers, those that share the most leading bits with prefix
// first, and those that share as many in the order of their text.
std::vector<std::string> best_first(const std::vector<const StandInPeer*>& peers,
                                    const HashPrefix& prefix) {
    std::vector<Identifier> node_ids;

    std::transform(peers.begin(), peers.end(), std::back_inserter(node_ids),
                   [](const StandInPeer* peer) { return peer->id(); });
    std::sort(node_ids.begin(), node_ids.end(),
              [&prefix](const Identifier& one, const Identifier& other) {
        const auto one_bits = prefix.matching_bits(one);
        const auto other_bits = prefix.matching_bits(other);

        return one_bits != other_bits ? one_bits > other_bits
                                      : one.to_string() < other.to_string();
    });
    return texts_of(node_ids);
}

// Settings making two passes, a second apart, held off a minute after.
PeerSearch::Settings two_passes() {
    PeerSearch::Settings settings;

    settings.passes = 2;
    settings.pass_interval = std::chrono::seconds(1);
    settings.hold = std::chrono::minutes(1);
    return settings;
}

// Why a search of the peers refuses settings and clock: nothing when it
// takes them.
std::optional<std::string> refusal(const PeerSearch::Settings& settings,
                                   PeerSearch::ClockFunction clock = &Clock::now) {
    try {
        (void)PeerSearch(settings, std::move(clock));
    } catch (const std::invalid_argument& error) {
        return error.what();
    }

    return std::nullopt;
}

// Each test has this node, with a key of its own, and a store that holds
// its public key and its peers'; a clock that stands still until a test
// moves it on; and a channel that takes what the peers answered.
class PeerSearchTest : public testing::Test {
protected:
    PeerSearchTest()
        : m_store(std::make_shared<fixtures::MemorySource>())
        , m_this_node(m_directory.path() / "this-node.pem", *m_store)
        , m_answered(SearchAnswered::name) {}

    // A peer that answers as answering says. Each peer takes one session.
    const StandInPeer& add_peer(Answering answering) {
        const auto name = "peer-" + std::to_string(m_peers.size()) + ".pem";
        const auto key_file = m_directory.path() / name;

        m_peers.push_back(
            std::make_unique<StandInPeer>(key_file, *m_store, std::move(answering), m_asked));
        return *m_peers.back();
    }

    // A session with peer.
    [[nodiscard]] std::shared_ptr<PeerSession> session_with(const StandInPeer& peer) const {
        return std::make_shared<PeerSession>(
            std::make_unique<PeerConnection>(peer.address(), PeerConnection::Limits{}),
            peer.id(), MessageSigner(m_this_node),
            MessageVerifier(std::make_shared<KnownKeys>(m_store), {}));
    }

    // A session with each of peers, in their order.
    [[nodiscard]] PeerSearch::Sessions sessions_with(
        const std::vector<const StandInPeer*>& peers) const {
        PeerSearch::Sessions sessions;

        std::transform(peers.begin(), peers.end(), std::back_inserter(sessions),
                       [this](const StandInPeer* peer) { return session_with(*peer); });
        return sessions;
    }

    // The test's clock.
    [[nodiscard]] PeerSearch::ClockFunction clock() const {
        return [this] { return m_time; };
    }

    [[nodiscard]] Clock::time_point now() const {
        return m_time;
    }

    void advance(Milliseconds duration) {
        m_time += duration;
    }

    // The requests the peers were sent since this was last called.
    [[nodiscard]] std::vector<Asked> asked() {
        return m_asked.taken();
    }

    // What was posted under data.search_answered since this was last
    // called, in order.
    [[nodiscard]] std::vector<SearchAnswered> answered() {
        std::vector<SearchAnswered> events;

        while (auto event = m_answered.next<SearchAnswered>(Milliseconds::zero())) {
            events.push_back(std::move(*event));
        }

        return events;
    }

private:
    fixtures::ScratchDirectory m_directory;
    std::shared_ptr<fixtures::MemorySource> m_store;
    NodeIdentity m_this_node;
    Requests m_asked;
    std::vector<std::unique_ptr<StandInPeer>> m_peers;
    Channel m_answered;
    Clock::time_point m_time;
};

} // namespace

// NOLINTBEGIN(bugprone-unchecked-optional-access)
// Each test reads an optional only after ASSERT_TRUE has checked that it
// holds a value, which the check does not follow.

TEST_F(PeerSearchTest, AsksEachPeerBestMatchFirst) {
    const auto& first = add_peer(finding({identifier_of('a')}));
    const auto& second = add_peer(finding({identifier_of('b')}));
    const auto& third = add_peer(finding({identifier_of('c')}));
    const std::vector<const StandInPeer*> peers{&first, &second, &third};
    // The second's own id, which no other matches as well.
    const auto prefix = HashPrefix::parse(second.id().hash());
    const auto sessions = sessions_with(peers);
    const PeerSearch::Settings defaults;
    PeerSearch search(defaults, clock());

    EXPECT_TRUE(search.start(prefix));

    const auto next = search.make_passes(sessions);
    const auto requests = asked();

    const std::vector<std::string> searches(peers.size(), "GET /data/search/" + prefix.to_string());

    ASSERT_EQ(requests.size(), peers.size());
    EXPECT_EQ(requests.at(0).node_id, second.id().to_string());
    EXPECT_EQ(node_ids_of(requests), best_first(peers, prefix));
    EXPECT_EQ(requests_of(requests), searches);
    EXPECT_EQ(relays_of(requests), (std::vector<std::string>{"2", "2", "2"}));
    ASSERT_TRUE(next.has_value());
    EXPECT_EQ(*next, now() + PeerSearch::Settings::default_pass_interval);
}

TEST_F(PeerSearchTest, PostsWhatEachPeerAnswered) {
    const auto& finder = add_peer(finding({identifier_of('a'), identifier_of('b')}));
    const auto& other = add_peer(finding({identifier_of('c')}));
    const auto sessions = sessions_with({&finder, &other});
    // The finder's own id, so that it is asked first.
    const auto prefix = HashPrefix::parse(finder.id().hash());
    const PeerSearch::Settings defaults;
    PeerSearch search(defaults, clock());

    EXPECT_TRUE(search.start(prefix));
    (void)search.make_passes(sessions);

    const auto events = answered();

    ASSERT_EQ(events.size(), 2U);
    EXPECT_EQ(events.at(0).prefix.to_string(), finder.id().hash());
    EXPECT_EQ(events.at(0).node.to_string(), finder.id().to_string());
    EXPECT_EQ(texts_of(events.at(0).results),
              texts_of({identifier_of('a'), identifier_of('b')}));
    EXPECT_EQ(events.at(1).node.to_string(), other.id().to_string());
    EXPECT_EQ(texts_of(events.at(1).results), texts_of({identifier_of('c')}));
}

TEST_F(PeerSearchTest, MakesEachPassApartThenEnds) {
    const auto& peer = add_peer(finding({identifier_of('a')}));
    const auto sessions = sessions_with({&peer});
    const auto settings = two_passes();
    PeerSearch search(settings, clock());

    EXPECT_FALSE(search.make_passes(sessions).has_value());
    EXPECT_TRUE(search.start(HashPrefix::parse("a")));

    const auto next = search.make_passes(sessions);

    ASSERT_TRUE(next.has_value());
    EXPECT_EQ(*next, now() + settings.pass_interval);
    EXPECT_EQ(asked().size(), 1U);
    EXPECT_EQ(answered().size(), 1U);

    // Not yet due.
    advance(settings.pass_interval - Milliseconds(1));
    EXPECT_EQ(search.make_passes(sessions), next);
    EXPECT_TRUE(asked().empty());

    advance(Milliseconds(1));
    EXPECT_FALSE(search.make_passes(sessions).has_value());
    EXPECT_EQ(asked().size(), 1U);
    EXPECT_EQ(answered().size(), 1U);

    advance(settings.pass_interval);
    EXPECT_FALSE(search.make_passes(sessions).has_value());
    EXPECT_TRUE(asked().empty());
}

TEST_F(PeerSearchTest, MakesThePassDueSoonestFirst) {
    const auto& peer = add_peer(finding({identifier_of('a')}));
    const auto sessions = sessions_with({&peer});
    const auto settings = two_passes();
    PeerSearch search(settings, clock());

    EXPECT_TRUE(search.start(HashPrefix::parse("b")));
    (void)search.make_passes(sessions);
    EXPECT_EQ(asked().size(), 1U);
    advance(settings.pass_interval + Milliseconds(1));
    EXPECT_TRUE(search.start(HashPrefix::parse("a")));

    // b's second pass has been due longer than a's first.
    const auto next = search.make_passes(sessions);
    const auto requests = asked();

    ASSERT_EQ(requests.size(), 2U);
    EXPECT_EQ(requests.at(0).request, "GET /data/search/b");
    EXPECT_EQ(requests.at(1).request, "GET /data/search/a");
    ASSERT_TRUE(next.has_value());
    EXPECT_EQ(*next, now() + settings.pass_interval);
}

TEST_F(PeerSearchTest, StartsNoneWhileOneIsUnderWayOrJustEnded) {
    const auto& peer = add_peer(finding({identifier_of('a')}));
    const auto sessions = sessions_with({&peer});
    const auto settings = two_passes();
    const auto prefix = HashPrefix::parse("a");
    const PeerSearch::Asker asker{identifier_of('f'), 2};
    PeerSearch search(settings, clock());

    EXPECT_TRUE(search.start(prefix));
    EXPECT_FALSE(search.start(prefix));
    EXPECT_FALSE(search.start(HashPrefix::parse("A"), asker));
    EXPECT_TRUE(search.start(HashPrefix::parse("ab")));

    (void)search.make_passes(sessions);
    advance(settings.pass_interval);
    EXPECT_FALSE(search.make_passes(sessions).has_value());

    EXPECT_FALSE(search.start(prefix));
    advance(settings.hold - Milliseconds(1));
    EXPECT_FALSE(search.start(prefix, asker));
    advance(Milliseconds(1));
    EXPECT_TRUE(search.start(prefix));
}

// Each search is for a prefix of its own, so that none is held off.
TEST_F(PeerSearchTest, TellsPeersOneFewerRelaysThanTheyWereTold) {
    const auto& peer = add_peer(finding({identifier_of('a')}));
    const auto sessions = sessions_with({&peer});
    const auto asker = identifier_of('f');
    const PeerSearch::Settings defaults;
    PeerSearch search(defaults, clock());

    EXPECT_TRUE(search.start(HashPrefix::parse("a")));
    EXPECT_TRUE(search.start(HashPrefix::parse("b"), PeerSearch::Asker{asker, 2}));
    EXPECT_TRUE(search.start(HashPrefix::parse("c"), PeerSearch::Asker{asker, 1}));
    // More than this node allows itself is taken as what it allows.
    EXPECT_TRUE(search.start(HashPrefix::parse("d"), PeerSearch::Asker{asker, 3}));
    EXPECT_FALSE(search.start(HashPrefix::parse("e"), PeerSearch::Asker{asker, 0}));

    (void)search.make_passes(sessions);

    const auto requests = asked();

    ASSERT_EQ(requests.size(), 4U);
    EXPECT_EQ(requests.at(0).request, "GET /data/search/a");
    EXPECT_EQ(relays_of(requests), (std::vector<std::string>{"2", "1", "0", "1"}));
}

TEST_F(PeerSearchTest, PassesOnNoMoreThanItIsAllowed) {
    const auto& peer = add_peer(finding({identifier_of('a')}));
    const auto sessions = sessions_with({&peer});
    PeerSearch::Settings settings;

    settings.relays = 0;

    PeerSearch search(settings, clock());

    EXPECT_FALSE(search.start(HashPrefix::parse("b"), PeerSearch::Asker{identifier_of('f'), 2}));
    EXPECT_TRUE(search.start(HashPrefix::parse("a")));
    (void)search.make_passes(sessions);
    EXPECT_EQ(relays_of(asked()), (std::vector<std::string>{"0"}));
}

TEST_F(PeerSearchTest, LeavesOutThePeerThatAsked) {
    const auto& asker = add_peer(finding({identifier_of('a')}));
    const auto& other = add_peer(finding({identifier_of('b')}));
    const auto sessions = sessions_with({&asker, &other});
    const PeerSearch::Settings defaults;
    PeerSearch search(defaults, clock());

    EXPECT_TRUE(search.start(HashPrefix::parse("a"), PeerSearch::Asker{asker.id(), 2}));
    (void)search.make_passes(sessions);
    EXPECT_EQ(node_ids_of(asked()), (std::vector<std::string>{other.id().to_string()}));
}

// What cannot be read is left out, and a peer whose answer was not HTTP, or
// not its own, is asked no more, as its session ended.
TEST_F(PeerSearchTest, PostsOnlyAnswersNamingSomething) {
    const auto& finder = add_peer(finding({identifier_of('a')}));
    const auto& empty = add_peer(finding({}));
    const auto& missing = add_peer(answering_with(ResponseCode::Status::NotFound));
    const auto& unreadable = add_peer(not_a_search_answer);
    const auto& garbled = add_peer(not_http);
    const auto& impostor = add_peer(unsigned_answer);
    const auto sessions =
        sessions_with({&finder, &empty, &missing, &unreadable, &garbled, &impostor});
    const auto settings = two_passes();
    PeerSearch search(settings, clock());

    EXPECT_TRUE(search.start(HashPrefix::parse("a")));
    (void)search.make_passes(sessions);

    const auto events = answered();

    EXPECT_EQ(asked().size(), 6U);
    ASSERT_EQ(events.size(), 1U);
    EXPECT_EQ(events.at(0).node.to_string(), finder.id().to_string());

    advance(settings.pass_interval);
    (void)search.make_passes(sessions);
    EXPECT_EQ(asked().size(), 4U);
    EXPECT_EQ(answered().size(), 1U);
}

TEST_F(PeerSearchTest, AsksPeersAsTheyAreConnected) {
    const auto& first = add_peer(finding({identifier_of('a')}));
    const auto& later = add_peer(finding({identifier_of('b')}));
    const auto sessions = sessions_with({&first, &later});
    const auto settings = two_passes();
    PeerSearch search(settings, clock());

    EXPECT_TRUE(search.start(HashPrefix::parse("a")));
    (void)search.make_passes({sessions.at(0), nullptr});
    EXPECT_EQ(node_ids_of(asked()), (std::vector<std::string>{first.id().to_string()}));

    sessions.at(0)->close();
    advance(settings.pass_interval);
    (void)search.make_passes(sessions);
    EXPECT_EQ(node_ids_of(asked()), (std::vector<std::string>{later.id().to_string()}));
}

// NOLINTEND(bugprone-unchecked-optional-access)

TEST_F(PeerSearchTest, SettingsThePythonNodeRefusesAreRefused) {
    auto too_few_passes = PeerSearch::Settings{};
    auto negative_interval = PeerSearch::Settings{};
    auto no_hold = PeerSearch::Settings{};

    too_few_passes.passes = 1;
    negative_interval.pass_interval = Milliseconds(-1);
    no_hold.hold = Milliseconds::zero();

    EXPECT_EQ(refusal(too_few_passes), "peers.search_passes is less than 2");
    EXPECT_EQ(refusal(negative_interval), "network.retry_after_seconds is less than zero");
    EXPECT_EQ(refusal(no_hold), "peers.broadcast_hold_seconds is not more than zero");
    EXPECT_EQ(refusal(PeerSearch::Settings(), nullptr), "A search of the peers needs the time");
    EXPECT_EQ(refusal(two_passes()), std::nullopt);
}

TEST_F(PeerSearchTest, PassesMayFollowAtOnce) {
    auto settings = PeerSearch::Settings{};

    settings.pass_interval = Milliseconds::zero();

    PeerSearch search(settings);

    EXPECT_TRUE(search.start(HashPrefix::parse("a")));
    // With no peers, every pass is made at once, and the search ends.
    EXPECT_FALSE(search.make_passes({}).has_value());
    EXPECT_FALSE(search.start(HashPrefix::parse("a")));
}
