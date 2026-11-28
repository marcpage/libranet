#include <gtest/gtest.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <exception>
#include <functional>
#include <future>
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
#include "msg/Channel.hpp"
#include "msg/Message.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "peer/Fetch.hpp"
#include "peer/PeerConnection.hpp"
#include "peer/PeerSession.hpp"
#include "storage/DataStored.hpp"
#include "storage/Identifier.hpp"
#include "storage/Zlib.hpp"
#include "storage/tests/Fixtures.hpp"
#include "types/Converter.hpp"

using http::Response;
using identity::KnownKeys;
using identity::MessageSigner;
using identity::MessageVerifier;
using identity::NodeIdentity;
using msg::Channel;
using net::Address;
using net::ISocket;
using peer::Fetch;
using peer::PeerConnection;
using peer::PeerSession;
using storage::DataStored;
using storage::Identifier;
using storage::Zlib;
using types::Converter;

// The tests only take DataStored, which Fetch posts from another file, so
// nothing here would make its message's virtual to_string(), which next<>()
// casts to.
template class msg::Message<DataStored>;

namespace {

using Clock = Fetch::Clock;
using Milliseconds = Fetch::Milliseconds;
using Outcome = Fetch::Outcome;
using Session = Fetch::Session;
using Status = http::ResponseCode::Status;

// How long a test waits, at most, for another thread, so that it fails
// rather than waits forever when what it waits for does not come.
constexpr auto patience = std::chrono::seconds(10);

// The header that gives a body's length, as a line of a message's head.
constexpr std::string_view length_line = "Content-Length: ";

// What a peer holds, and the node asking for it does not.
constexpr std::string_view content_text = "Content only a peer holds";

// What a node does as it answers each request, before its answer is sent.
using Answering = std::function<void()>;

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
// closes. It keeps the line of each request it was sent.
class StandInNode {
public:
    // Answers each request with answer, the text of a response, once
    // answering, if given, has run.
    explicit StandInNode(std::string answer, Answering answering = nullptr);
    ~StandInNode();

    [[nodiscard]] Address address() const;
    // The line of each request it has been sent, the first first.
    [[nodiscard]] std::vector<std::string> requests() const;
    [[nodiscard]] std::size_t asked() const;
    // Whether it has been sent count requests, waiting for them at most
    // patience.
    [[nodiscard]] bool is_asked(std::size_t count) const;

    StandInNode(const StandInNode&) = delete;
    StandInNode& operator=(const StandInNode&) = delete;
    StandInNode(StandInNode&&) = delete;
    StandInNode& operator=(StandInNode&&) = delete;

private:
    ISocket::Ptr m_listener;
    std::string m_answer;
    Answering m_answering;
    mutable std::mutex m_mutex;
    mutable std::condition_variable m_asked_more;
    std::vector<std::string> m_requests; // Guarded by m_mutex
    std::thread m_thread;

    void _answer_all();
    void _keep(const std::string& request);
};

StandInNode::StandInNode(std::string answer, Answering answering)
    : m_listener(ISocket::bind(Address("127.0.0.1", 0)))
    , m_answer(std::move(answer))
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

std::vector<std::string> StandInNode::requests() const {
    const std::scoped_lock lock(m_mutex);

    return m_requests;
}

std::size_t StandInNode::asked() const {
    return requests().size();
}

bool StandInNode::is_asked(std::size_t count) const {
    std::unique_lock lock(m_mutex);

    const auto has_all = [this, count] { return m_requests.size() >= count; };

    return m_asked_more.wait_for(lock, patience, has_all);
}

void StandInNode::_answer_all() {
    try {
        const auto connection = m_listener->accept();

        connection->set_receive_timeout(patience);

        while (const auto request = read_request(*connection)) {
            _keep(*request);

            if (m_answering) {
                m_answering();
            }

            connection->send_all(m_answer);
        }
        // The node asking may end the connection while answers are on their
        // way, as a session does once one fails its check.
        // NOLINTNEXTLINE(bugprone-empty-catch)
    } catch (const std::system_error&) {
    } catch (const std::exception& error) {
        ADD_FAILURE() << "The node failed to answer: " << error.what();
    }
}

void StandInNode::_keep(const std::string& request) {
    {
        const std::scoped_lock lock(m_mutex);

        m_requests.push_back(request.substr(0, request.find("\r\n")));
    }

    m_asked_more.notify_all();
}

// A clock that stands still until a test, or a node it stands in for, moves
// it on. Any thread may read it or move it.
class StillClock {
public:
    [[nodiscard]] Fetch::ClockFunction function() const;
    void advance(Milliseconds elapsed);

private:
    Clock::time_point m_start{Clock::now()};
    std::atomic<Milliseconds::rep> m_elapsed{0};
};

Fetch::ClockFunction StillClock::function() const {
    return [this] { return m_start + Milliseconds(m_elapsed.load()); };
}

void StillClock::advance(Milliseconds elapsed) {
    m_elapsed += elapsed.count();
}

// The sessions a test has connected.
class Connected {
public:
    explicit Connected(std::vector<Session> sessions = {});

    [[nodiscard]] Fetch::Peers peers();

private:
    std::mutex m_mutex;
    std::vector<Session> m_sessions; // Guarded by m_mutex
};

Connected::Connected(std::vector<Session> sessions)
    : m_sessions(std::move(sessions)) {}

Fetch::Peers Connected::peers() {
    return [this] {
        const std::scoped_lock lock(m_mutex);

        return m_sessions;
    };
}

// The text of an answer of status with body, and a Retry-After when one is
// given, signed by signer.
std::string answer(const MessageSigner& signer, Status status, const std::string& body = "",
                   const std::string& retry_after = "") {
    Response made;

    made.set_status_code(status);
    made.headers().set("Content-Length", std::to_string(body.size()));

    if (!retry_after.empty()) {
        made.headers().set("Retry-After", retry_after);
    }

    made.set_body(body);
    signer.sign(made);
    return made.to_string();
}

// The answer of a node that holds body.
std::string sending(const MessageSigner& signer, std::string_view body) {
    return answer(signer, Status::Ok, std::string(body));
}

// The answer of a node that does not hold what it is asked for.
std::string lacking(const MessageSigner& signer) {
    return answer(signer, Status::NotFound);
}

// The answer of a node that is looking for what it is asked for, asking to
// be left for retry_after seconds.
std::string looking(const MessageSigner& signer, const std::string& retry_after) {
    return answer(signer, Status::ServiceUnavailable, "", retry_after);
}

// Moves clock on a second, as a node that takes that long to answer.
Answering taking_a_second(StillClock& clock) {
    return [&clock] { clock.advance(std::chrono::seconds(1)); };
}

// Sets answered.
Answering noting(std::atomic<bool>& answered) {
    return [&answered] { answered = true; };
}

// Waits for released to be set.
Answering waiting_for(std::shared_future<void> released) {
    return [released = std::move(released)] {
        EXPECT_EQ(released.wait_for(patience), std::future_status::ready);
    };
}

// The request line of a GET of identifier's content.
std::string get_of(const Identifier& identifier) {
    return "GET /data/" + identifier.to_string() + " HTTP/1.1";
}

// What a test fetches with but where it says otherwise: no wait for a peer
// that gave no Retry-After, nor for a pass to begin.
Fetch::Settings quick() {
    Fetch::Settings settings;

    settings.retry_after = Milliseconds(0);
    settings.failed_search_hold = std::chrono::minutes(1);
    return settings;
}

// Each test has this node and three peers, each with a key of its own, a
// store that holds their public keys, and a store of what this node holds,
// which holds nothing to start with.
class FetchTest : public testing::Test {
protected:
    FetchTest()
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

    // A fetch of the peers connected gives, keeping what it finds in
    // store().
    [[nodiscard]] Fetch fetch_from(Connected& connected, const Fetch::Settings& settings = quick(),
                                   Fetch::ClockFunction clock = &Clock::now) const {
        return {m_store, connected.peers(), settings, std::move(clock)};
    }

    // peer's public key, as the content its id names.
    [[nodiscard]] std::string key_of(const NodeIdentity& peer) const {
        const auto key = m_keys->get(peer.id());

        return key ? Converter::text_of(*key) : std::string();
    }

    // The text of what store() holds under identifier, if anything.
    [[nodiscard]] std::optional<std::string> held(const Identifier& identifier) const {
        const auto body = m_store->get(identifier);

        if (!body) {
            return std::nullopt;
        }

        return Converter::text_of(*body);
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
};

// The identifier of content_text.
Identifier content_id() {
    return Identifier::of(Converter::bytes_of(content_text));
}

// Whether making a fetch with settings is refused.
bool refused(const Fetch::Settings& settings) {
    try {
        Connected connected;
        const Fetch fetch(std::make_shared<fixtures::MemorySource>(), connected.peers(),
                          settings);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

} // namespace

// NOLINTBEGIN(bugprone-unchecked-optional-access)

TEST_F(FetchTest, ContentSentIsStoredAndPosted) {
    Channel stored(DataStored::name);
    const StandInNode peer(sending(MessageSigner(first()), content_text));
    Connected connected({session_with(peer, first())});
    auto fetch = fetch_from(connected);

    EXPECT_EQ(fetch.fetch(content_id()), Outcome::Found);
    EXPECT_EQ(held(content_id()), content_text);
    ASSERT_EQ(peer.asked(), 1U);
    EXPECT_EQ(peer.requests().at(0), get_of(content_id()));

    const auto posted = stored.next<DataStored>(patience);

    ASSERT_TRUE(posted);
    EXPECT_EQ(posted->identifier.to_string(), content_id().to_string());
    ASSERT_TRUE(posted->node);
    EXPECT_EQ(posted->node->to_string(), first().id().to_string());
    EXPECT_EQ(posted->size, content_text.size());
    EXPECT_FALSE(fetch.is_held_off(content_id()));
}

// Content sent compressed is kept as it came, so that content over 1 MiB is
// kept compressed (High-Level Design §4.3).
TEST_F(FetchTest, CompressedContentIsStoredAsSent) {
    const auto compressed = Converter::text_of(
        Zlib::compress(Converter::bytes_of(content_text), Zlib::balance_level));
    Channel stored(DataStored::name);
    const StandInNode peer(sending(MessageSigner(first()), compressed));
    Connected connected({session_with(peer, first())});
    auto fetch = fetch_from(connected);

    EXPECT_EQ(fetch.fetch(content_id()), Outcome::Found);
    EXPECT_EQ(held(content_id()), compressed);

    const auto posted = stored.next<DataStored>(patience);

    ASSERT_TRUE(posted);
    EXPECT_EQ(posted->size, compressed.size());
}

// NOLINTEND(bugprone-unchecked-optional-access)

// The peer is asked in each pass, as it may have fetched the content since.
TEST_F(FetchTest, WhatIsNotTheContentIsNotStored) {
    const Channel stored(DataStored::name);
    const StandInNode peer(sending(MessageSigner(first()), "Something else"));
    Connected connected({session_with(peer, first())});
    auto fetch = fetch_from(connected);

    EXPECT_EQ(fetch.fetch(content_id()), Outcome::NotFound);
    EXPECT_FALSE(store()->has(content_id()));
    EXPECT_TRUE(stored.empty());
    EXPECT_EQ(peer.asked(), 3U);
}

// The content sought is the second peer's key, which its id names, so its id
// matches best, though the first peer is connected first.
TEST_F(FetchTest, BestMatchIsAskedFirst) {
    const auto sought = second().id();
    const StandInNode first_peer(lacking(MessageSigner(first())));
    const StandInNode second_peer(sending(MessageSigner(second()), key_of(second())));
    Connected connected({session_with(first_peer, first()), session_with(second_peer, second())});
    auto fetch = fetch_from(connected);

    EXPECT_EQ(fetch.fetch(sought), Outcome::Found);
    EXPECT_EQ(first_peer.asked(), 0U);
    ASSERT_EQ(second_peer.asked(), 1U);
    EXPECT_EQ(second_peer.requests().at(0), get_of(sought));
}

TEST_F(FetchTest, EachPeerIsAskedInEachPass) {
    constexpr std::size_t passes = 4;
    auto settings = quick();
    const StandInNode first_peer(lacking(MessageSigner(first())));
    const StandInNode second_peer(lacking(MessageSigner(second())));
    Connected connected({session_with(first_peer, first()), session_with(second_peer, second())});

    settings.passes = passes;

    auto fetch = fetch_from(connected, settings);

    EXPECT_EQ(fetch.fetch(content_id()), Outcome::NotFound);
    EXPECT_EQ(first_peer.asked(), passes);
    EXPECT_EQ(second_peer.asked(), passes);
}

// The third peer moves the clock on a second as it answers, so a second
// passes in each pass: the second peer, which asked to be left that long, is
// asked in each, and the first, which asked to be left a minute, only once.
TEST_F(FetchTest, PeerIsLeftUntilItsRetryAfterHasPassed) {
    StillClock clock;
    const StandInNode first_peer(looking(MessageSigner(first()), "60"));
    const StandInNode second_peer(looking(MessageSigner(second()), "1"));
    const StandInNode third_peer(lacking(MessageSigner(third())), taking_a_second(clock));
    Connected connected({
        session_with(first_peer, first()),
        session_with(second_peer, second()),
        session_with(third_peer, third()),
    });
    auto fetch = fetch_from(connected, quick(), clock.function());

    EXPECT_EQ(fetch.fetch(content_id()), Outcome::NotFound);
    EXPECT_EQ(first_peer.asked(), 1U);
    EXPECT_EQ(second_peer.asked(), 3U);
    EXPECT_EQ(third_peer.asked(), 3U);
}

TEST_F(FetchTest, SearchThatFoundNothingIsHeldOff) {
    StillClock clock;
    const StandInNode peer(lacking(MessageSigner(first())));
    Connected connected({session_with(peer, first())});
    auto fetch = fetch_from(connected, quick(), clock.function());

    EXPECT_EQ(fetch.fetch(content_id()), Outcome::NotFound);
    EXPECT_TRUE(fetch.is_held_off(content_id()));
    EXPECT_EQ(fetch.fetch(content_id()), Outcome::HeldOff);
    EXPECT_EQ(peer.asked(), 3U);

    clock.advance(quick().failed_search_hold);

    EXPECT_FALSE(fetch.is_held_off(content_id()));
    EXPECT_EQ(fetch.fetch(content_id()), Outcome::NotFound);
    EXPECT_EQ(peer.asked(), 6U);
}

// A search that asked no peer set off no search of theirs, which could start
// it again, so another may start at once.
TEST_F(FetchTest, SearchThatAskedNoPeerIsNotHeldOff) {
    Connected connected;
    auto fetch = fetch_from(connected);

    EXPECT_EQ(fetch.fetch(content_id()), Outcome::NotFound);
    EXPECT_FALSE(fetch.is_held_off(content_id()));
}

TEST_F(FetchTest, ContentHeldIsNotAskedFor) {
    const StandInNode peer(sending(MessageSigner(first()), content_text));
    Connected connected({session_with(peer, first())});
    auto fetch = fetch_from(connected);

    store()->put(content_id(), Converter::bytes_of(content_text));

    EXPECT_EQ(fetch.fetch(content_id()), Outcome::Found);
    EXPECT_EQ(peer.asked(), 0U);
}

// The second peer connects as the first answers, and is then asked in the
// same pass.
TEST_F(FetchTest, PeerThatConnectsDuringTheSearchIsAsked) {
    std::atomic<bool> answered{false};
    const StandInNode first_peer(lacking(MessageSigner(first())), noting(answered));
    const StandInNode second_peer(sending(MessageSigner(second()), content_text));
    const auto first_session = session_with(first_peer, first());
    const auto second_session = session_with(second_peer, second());
    const Fetch::Peers peers = [&] {
        return answered ? std::vector<Session>{first_session, second_session}
                        : std::vector<Session>{first_session};
    };
    Fetch fetch(store(), peers, quick());

    EXPECT_EQ(fetch.fetch(content_id()), Outcome::Found);
    EXPECT_EQ(first_peer.asked(), 1U);
    EXPECT_EQ(second_peer.asked(), 1U);
}

// The first peer, whose id the content sought names, is asked first, and its
// unsigned answer ends its session.
TEST_F(FetchTest, PeerThatCannotBeAskedIsPassedOver) {
    const auto sought = first().id();
    const StandInNode first_peer("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n");
    const StandInNode second_peer(sending(MessageSigner(second()), key_of(first())));
    const auto first_session = session_with(first_peer, first());
    Connected connected({first_session, session_with(second_peer, second())});
    auto fetch = fetch_from(connected);

    EXPECT_EQ(fetch.fetch(sought), Outcome::Found);
    EXPECT_EQ(first_peer.asked(), 1U);
    EXPECT_FALSE(first_session->is_open());
    EXPECT_EQ(second_peer.asked(), 1U);
}

TEST_F(FetchTest, SearchUnderWayIsNotStartedAgain) {
    std::promise<void> release;
    const StandInNode peer(sending(MessageSigner(first()), content_text),
                           waiting_for(release.get_future().share()));
    Connected connected({session_with(peer, first())});
    auto fetch = fetch_from(connected);
    auto searching = std::async(std::launch::async, [&fetch] { return fetch.fetch(content_id()); });

    ASSERT_TRUE(peer.is_asked(1));
    EXPECT_EQ(fetch.fetch(content_id()), Outcome::Searching);

    release.set_value();

    EXPECT_EQ(searching.get(), Outcome::Found);
    EXPECT_EQ(peer.asked(), 1U);
}

// The peer asks to be left a minute, so the search waits that long for its
// next pass, until stopped.
TEST_F(FetchTest, StopEndsTheSearches) {
    auto settings = quick();
    const StandInNode peer(looking(MessageSigner(first()), "60"));
    Connected connected({session_with(peer, first())});

    settings.retry_after = std::chrono::minutes(1);
    settings.failed_search_hold = Fetch::Settings::default_failed_search_hold;

    auto fetch = fetch_from(connected, settings);
    auto searching = std::async(std::launch::async, [&fetch] { return fetch.fetch(content_id()); });

    ASSERT_TRUE(peer.is_asked(1));
    fetch.stop();

    ASSERT_EQ(searching.wait_for(patience), std::future_status::ready);
    EXPECT_EQ(searching.get(), Outcome::Stopped);
    EXPECT_FALSE(fetch.is_held_off(content_id()));
    EXPECT_EQ(fetch.fetch(content_id()), Outcome::Stopped);
    EXPECT_EQ(peer.asked(), 1U);
}

// With 3 passes and a retry after of a minute, a search may last two
// minutes, which the failed search hold must outlast.
TEST_F(FetchTest, SettingsThatCannotBeUsedAreRefused) {
    auto one_pass = quick();
    auto negative_retry_after = quick();
    auto short_hold = quick();
    auto no_hold = quick();

    one_pass.passes = 1;
    negative_retry_after.retry_after = Milliseconds(-1);
    short_hold.retry_after = std::chrono::minutes(1);
    short_hold.failed_search_hold = std::chrono::minutes(2);
    no_hold.failed_search_hold = Milliseconds(0);

    EXPECT_FALSE(refused(quick()));
    EXPECT_TRUE(refused(one_pass));
    EXPECT_TRUE(refused(negative_retry_after));
    EXPECT_TRUE(refused(short_hold));
    EXPECT_TRUE(refused(no_hold));
}

TEST_F(FetchTest, NeedsAStorePeersAndTheTime) {
    Connected connected;

    EXPECT_THROW((void)Fetch(nullptr, connected.peers(), quick()), std::invalid_argument);
    EXPECT_THROW((void)Fetch(store(), nullptr, quick()), std::invalid_argument);
    EXPECT_THROW((void)Fetch(store(), connected.peers(), quick(), nullptr),
                 std::invalid_argument);
}
