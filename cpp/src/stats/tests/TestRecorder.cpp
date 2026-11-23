#include <gtest/gtest.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <iterator>
#include <map>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "db/Database.hpp"
#include "db/tests/Fixtures.hpp"
#include "eviction/Evictor.hpp"
#include "msg/Channel.hpp"
#include "msg/Message.hpp"
#include "protocol/NodeList.hpp"
#include "protocol/SeekList.hpp"
#include "stats/ContentUses.hpp"
#include "stats/NodeAddresses.hpp"
#include "stats/Recorder.hpp"
#include "storage/DataDeleted.hpp"
#include "storage/DataNotFound.hpp"
#include "storage/DataRequested.hpp"
#include "storage/DataStored.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"

using db::Database;
using db::SqliteError;
using eviction::Evictor;
using fixtures::ScratchDirectory;
using msg::Channel;
using protocol::NodeList;
using protocol::SeekList;
using stats::ContentUses;
using stats::NodeAddresses;
using stats::Recorder;
using storage::DataDeleted;
using storage::DataNotFound;
using storage::DataRequested;
using storage::DataStored;
using storage::HashPrefix;
using storage::Identifier;
using namespace std::chrono_literals;

// The tests only take the lists the recorder posts, from another file, so
// nothing here would make their messages' virtual to_string(), which
// next<>() casts to.
template class msg::Message<Recorder::NodesUpdated>;
template class msg::Message<Recorder::CandidatesUpdated>;
template class msg::Message<Recorder::SeekUpdated>;
template class msg::Message<Evictor::Candidates>;

namespace {

using Candidate = NodeAddresses::Candidate;
using CandidatesRequested = Evictor::CandidatesRequested;
using CandidatesUpdated = Recorder::CandidatesUpdated;
using Clock = Recorder::Clock;
using ConnectionFailed = Recorder::ConnectionFailed;
using ConnectionOpened = Recorder::ConnectionOpened;
using NodesReceived = Recorder::NodesReceived;
using NodesUpdated = Recorder::NodesUpdated;
using SearchRequested = Recorder::SearchRequested;
using SeekUpdated = Recorder::SeekUpdated;
using Settings = Recorder::Settings;
using Source = NodeAddresses::Source;
using Texts = std::vector<std::string>;
// An entry of a node list, as endpoint and node id.
using Named = std::pair<std::string, std::string>;
// A candidate, as node id and endpoints.
using Dialable = std::pair<std::string, std::vector<std::string>>;

// How long a test waits for a list to be posted. A wait ends as soon as one
// is, so only a failing test waits this long; it is generous because a
// sanitizer build, sharing a slow machine with other tests, can take far
// longer than a test run alone.
constexpr auto post_timeout = 5s;

// How long a test watches for a list that should not be posted: many
// derive intervals.
constexpr auto quiet_time = 50ms;

// Short, so that the tests see many derivations.
constexpr auto derive_interval = 2ms;

// How long the tests seek each entry for.
constexpr auto seek_entry_ttl = 10s;

// How many bytes the content the tests store takes, and as much as content
// may take (HighLevelDesign §4.3).
constexpr std::size_t stored_size = 5;
constexpr std::size_t largest_size = 1'048'576;

// When the tests' clock starts, in whole seconds, as the table keeps times
// to the millisecond. With every time the same, the lists are in the order
// of their endpoints and node ids.
constexpr auto start = Clock::time_point(1'700'000'000s);

// Endpoints, at addresses set aside for documentation (RFC 5737).
constexpr std::string_view first = "http://203.0.113.1:4300";
constexpr std::string_view second = "http://203.0.113.2:4300";
constexpr std::string_view third = "http://203.0.113.3:4300";
constexpr std::string_view own_endpoint = "http://198.51.100.7:4300";

// The id of a node whose hash is digit, over and over.
Identifier node(char digit) {
    return {"sha256", std::string(Identifier::longest_hash_digits(), digit)};
}

// This node's own id.
Identifier own_id() {
    return node('0');
}

// The content whose hash is digit, over and over.
Identifier content(char digit) {
    return node(digit);
}

// The settings the tests derive with, and limits.
Settings test_settings(NodeAddresses::Limits limits = {}) {
    return {derive_interval, limits, seek_entry_ttl};
}

// A list naming each node of digits at the endpoint beside it.
NodeList list_of(const std::vector<std::pair<std::string_view, char>>& entries) {
    NodeList list;

    for (const auto& [endpoint, digit] : entries) {
        list.add(std::string(endpoint), node(digit));
    }

    return list;
}

// The entry naming the node of digit at endpoint.
Named named(std::string_view endpoint, char digit) {
    return {std::string(endpoint), node(digit).to_string()};
}

// The candidate dialing the node of digit at endpoints.
Dialable dialable(char digit, const std::vector<std::string>& endpoints) {
    return {node(digit).to_string(), endpoints};
}

// The endpoint and node id of each entry of list, in order.
std::vector<Named> entries_of(const NodeList& list) {
    std::vector<Named> entries;

    std::transform(list.entries().begin(), list.entries().end(), std::back_inserter(entries),
                   [](const NodeList::Entry& entry) {
        return Named(entry.endpoint, entry.node_id.to_string());
    });
    return entries;
}

// The node id and endpoints of each of candidates, in order.
std::vector<Dialable> dialable_of(const std::vector<Candidate>& candidates) {
    std::vector<Dialable> found;

    std::transform(candidates.begin(), candidates.end(), std::back_inserter(found),
                   [](const Candidate& candidate) {
        return Dialable(candidate.node_id.to_string(), candidate.endpoints);
    });
    return found;
}

// The text of each of entries, in order.
template<typename Entry>
Texts texts_of(const std::vector<Entry>& entries) {
    Texts texts;

    std::transform(entries.begin(), entries.end(), std::back_inserter(texts),
                   [](const Entry& entry) { return entry.to_string(); });
    return texts;
}

// Whether a Recorder refuses own_endpoints or settings as invalid.
bool refused(const ScratchDirectory& directory, const std::vector<std::string>& own_endpoints,
             Settings settings) {
    try {
        (void)Recorder(directory.database(), own_id(), own_endpoints, settings);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

// A recorder on a database of the test's own, with a clock that stands
// still until a test moves it on, and channels that take the lists it
// posts, made before it so that they take the first.
class RecorderTest : public testing::Test {
protected:
    // Starts the recorder, again if it was running, with settings.
    void start_recording(Settings new_settings = test_settings());
    // Stops the recorder, which closes the database.
    void stop_recording() noexcept;
    // Moves the clock on, which the recorder's thread reads.
    void advance(std::chrono::milliseconds duration) noexcept;

    // The next node list, candidates, or seek list posted. Throws
    // std::runtime_error if none is posted in time.
    [[nodiscard]] NodesUpdated next_nodes();
    [[nodiscard]] CandidatesUpdated next_candidates();
    [[nodiscard]] SeekUpdated next_seek();
    // Whether no list is posted for a while.
    [[nodiscard]] bool quiet();
    // The identifier and size of each object the recorder answers request
    // with, in order. Throws std::runtime_error if it does not answer in
    // time.
    [[nodiscard]] Texts answer_to(CandidatesRequested request);
    // The addresses of the node of digit, read from the database once the
    // recorder has stopped.
    [[nodiscard]] std::vector<NodeAddresses::Address> addresses_of(char digit) const;
    // What is known of the content of digit, read from the database once the
    // recorder has stopped.
    [[nodiscard]] std::optional<ContentUses::Uses> uses_of(char digit) const;

private:
    ScratchDirectory m_directory;
    Channel m_nodes{NodesUpdated::name};
    Channel m_candidates{CandidatesUpdated::name};
    Channel m_seek{SeekUpdated::name};
    Channel m_answers{Evictor::Candidates::name};
    // How far the clock has moved on from start.
    std::atomic<std::int64_t> m_elapsed_milliseconds{0};
    std::unique_ptr<Recorder> m_recorder;
};

void RecorderTest::start_recording(Settings new_settings) {
    stop_recording();
    m_recorder = std::make_unique<Recorder>(
        m_directory.database(), own_id(), std::vector<std::string>{std::string(own_endpoint)},
        new_settings, [this] { return start + std::chrono::milliseconds(m_elapsed_milliseconds); });
}

void RecorderTest::stop_recording() noexcept {
    m_recorder.reset();
}

void RecorderTest::advance(std::chrono::milliseconds duration) noexcept {
    m_elapsed_milliseconds += duration.count();
}

NodesUpdated RecorderTest::next_nodes() {
    auto posted = m_nodes.next<NodesUpdated>(post_timeout);

    if (!posted) {
        throw std::runtime_error("No node list was posted");
    }

    return std::move(*posted);
}

CandidatesUpdated RecorderTest::next_candidates() {
    auto posted = m_candidates.next<CandidatesUpdated>(post_timeout);

    if (!posted) {
        throw std::runtime_error("No candidates were posted");
    }

    return std::move(*posted);
}

SeekUpdated RecorderTest::next_seek() {
    auto posted = m_seek.next<SeekUpdated>(post_timeout);

    if (!posted) {
        throw std::runtime_error("No seek list was posted");
    }

    return std::move(*posted);
}

bool RecorderTest::quiet() {
    const auto nodes = m_nodes.next<NodesUpdated>(quiet_time);
    const auto candidates = m_candidates.next<CandidatesUpdated>(quiet_time);
    const auto seek = m_seek.next<SeekUpdated>(quiet_time);

    return !nodes && !candidates && !seek;
}

Texts RecorderTest::answer_to(CandidatesRequested request) {
    Channel::post(CandidatesRequested::name, std::move(request));

    const auto answer = m_answers.next<Evictor::Candidates>(post_timeout);
    Texts objects;

    if (!answer) {
        throw std::runtime_error("No candidates were listed");
    }

    std::transform(answer->objects.begin(), answer->objects.end(), std::back_inserter(objects),
                   [](const Evictor::Held& object) {
        return object.identifier.to_string() + " " + std::to_string(object.size);
    });
    return objects;
}

std::vector<NodeAddresses::Address> RecorderTest::addresses_of(char digit) const {
    Database database(m_directory.database(), NodeAddresses::schema);
    const NodeAddresses addresses(database, NodeAddresses::Limits());

    return addresses.addresses(node(digit));
}

std::optional<ContentUses::Uses> RecorderTest::uses_of(char digit) const {
    Database database(m_directory.database(), ContentUses::schema);
    const ContentUses content_uses(database);

    return content_uses.uses(node(digit));
}

// The recorder, once it has posted the lists it starts with.
class StartedRecorderTest : public RecorderTest {
protected:
    StartedRecorderTest();
};

StartedRecorderTest::StartedRecorderTest() {
    start_recording();
    (void)next_nodes();
    (void)next_candidates();
    (void)next_seek();
}

// The identifier and size of the content of digit, as a list of what to
// let go of names it.
std::string listed(char digit, std::size_t size) {
    return content(digit).to_string() + " " + std::to_string(size);
}

// Has the recorder hold, all stored now: b and c, small and never asked
// for, alike but for their hashes; d, as large as content may be; e, small
// and asked for twice; f, deleted since; and this node's own key. None of
// their hashes shares a leading bit with this node's id, so how far each
// is plays no part, and none has gone unused for any time, so neither does
// that.
void store_to_rank() {
    Channel::post(DataStored::name, DataStored{content('c'), std::nullopt, stored_size});
    Channel::post(DataStored::name, DataStored{content('b'), std::nullopt, stored_size});
    Channel::post(DataStored::name, DataStored{content('d'), std::nullopt, largest_size});
    Channel::post(DataStored::name, DataStored{content('e'), std::nullopt, stored_size});
    Channel::post(DataRequested::name, DataRequested{content('e')});
    Channel::post(DataRequested::name, DataRequested{content('e')});
    Channel::post(DataStored::name, DataStored{content('f'), std::nullopt, stored_size});
    Channel::post(DataDeleted::name, DataDeleted{content('f'), stored_size});
    Channel::post(DataStored::name, DataStored{own_id(), std::nullopt, stored_size});
}

} // namespace

TEST_F(RecorderTest, PostsTheListsAsItStarts) {
    start_recording();

    const auto nodes = next_nodes();
    const auto candidates = next_candidates();
    const auto seek = next_seek();

    EXPECT_EQ(entries_of(nodes.nodes), std::vector<Named>{named(own_endpoint, '0')});
    EXPECT_TRUE(nodes.added.entries().empty());
    EXPECT_TRUE(candidates.candidates.empty());
    EXPECT_TRUE(seek.seek.data().empty());
    EXPECT_TRUE(seek.seek.search().empty());
}

TEST_F(StartedRecorderTest, ListsThatHaveNotChangedAreNotPostedAgain) {
    EXPECT_TRUE(quiet());
}

TEST_F(StartedRecorderTest, TheAddressesAListNamesAreCandidates) {
    Channel::post(NodesReceived::name, NodesReceived{list_of({{second, 'b'}, {first, 'a'}}), {}});

    const auto candidates = next_candidates();

    EXPECT_EQ(dialable_of(candidates.candidates),
              (std::vector<Dialable>{dialable('a', {std::string(first)}),
                                     dialable('b', {std::string(second)})}));
}

TEST_F(StartedRecorderTest, EachAddressIsKeptAsItWasLearnedNotYetTried) {
    const std::map<std::string, Source> sources{{std::string(first), Source::Advertised}};

    Channel::post(NodesReceived::name,
                  NodesReceived{list_of({{first, 'a'}, {second, 'a'}}), sources});
    (void)next_candidates();
    stop_recording();

    const auto addresses = addresses_of('a');

    ASSERT_EQ(addresses.size(), 2U);
    EXPECT_EQ(addresses.at(0).endpoint, first);
    EXPECT_EQ(addresses.at(0).source, Source::Advertised);
    EXPECT_EQ(addresses.at(0).attempts, 0);
    EXPECT_EQ(addresses.at(1).endpoint, second);
    EXPECT_EQ(addresses.at(1).source, Source::Relayed);
    EXPECT_EQ(addresses.at(1).attempts, 0);
}

TEST_F(StartedRecorderTest, TheAddressesAreRememberedAfterARestart) {
    Channel::post(NodesReceived::name, NodesReceived{list_of({{first, 'a'}}), {}});
    (void)next_candidates();
    start_recording();
    (void)next_nodes();

    const auto candidates = next_candidates();

    EXPECT_EQ(dialable_of(candidates.candidates),
              std::vector<Dialable>{dialable('a', {std::string(first)})});
}

TEST_F(StartedRecorderTest, ANodeReachedIsNamedInTheNodeListAndAsNew) {
    Channel::post(ConnectionOpened::name, ConnectionOpened{node('a'), std::string(first)});

    const auto nodes = next_nodes();

    EXPECT_EQ(entries_of(nodes.nodes),
              (std::vector<Named>{named(own_endpoint, '0'), named(first, 'a')}));
    EXPECT_EQ(entries_of(nodes.added), std::vector<Named>{named(first, 'a')});
}

TEST_F(StartedRecorderTest, OnlyWhatTheListBeforeDidNotNameIsNew) {
    Channel::post(ConnectionOpened::name, ConnectionOpened{node('a'), std::string(first)});
    (void)next_nodes();
    Channel::post(ConnectionOpened::name, ConnectionOpened{node('b'), std::string(second)});

    const auto nodes = next_nodes();

    EXPECT_EQ(entries_of(nodes.nodes), (std::vector<Named>{named(own_endpoint, '0'),
                                                           named(first, 'a'), named(second, 'b')}));
    EXPECT_EQ(entries_of(nodes.added), std::vector<Named>{named(second, 'b')});
}

TEST_F(StartedRecorderTest, ANodeThatFailedSinceItWasReachedIsLeftOutOfTheNodeList) {
    Channel::post(ConnectionOpened::name, ConnectionOpened{node('a'), std::string(first)});
    (void)next_nodes();
    Channel::post(ConnectionFailed::name, ConnectionFailed{node('a'), std::string(first)});

    const auto nodes = next_nodes();

    EXPECT_EQ(entries_of(nodes.nodes), std::vector<Named>{named(own_endpoint, '0')});
    EXPECT_TRUE(nodes.added.entries().empty());
}

TEST_F(RecorderTest, AnAddressThatFailedTooOftenIsNoLongerACandidate) {
    start_recording(test_settings({1, 1}));
    (void)next_nodes();
    (void)next_candidates();
    (void)next_seek();
    Channel::post(NodesReceived::name, NodesReceived{list_of({{first, 'a'}}), {}});
    (void)next_candidates();
    Channel::post(ConnectionFailed::name, ConnectionFailed{node('a'), std::string(first)});

    const auto candidates = next_candidates();

    EXPECT_TRUE(candidates.candidates.empty());
    stop_recording();
    EXPECT_TRUE(addresses_of('a').empty());
}

// The recorder takes events in the order they are posted, so once a node
// list naming a node reached after them is posted, the content events
// before it are recorded.
TEST_F(StartedRecorderTest, HowContentIsUsedIsRecorded) {
    constexpr std::size_t size = 5;

    Channel::post(DataStored::name, DataStored{node('c'), node('a'), size});
    Channel::post(DataRequested::name, DataRequested{node('c')});
    Channel::post(DataRequested::name, DataRequested{node('c')});
    Channel::post(DataRequested::name, DataRequested{node('d')});
    Channel::post(ConnectionOpened::name, ConnectionOpened{node('a'), std::string(first)});
    (void)next_nodes();
    stop_recording();

    const auto stored = uses_of('c').value_or(ContentUses::Uses());
    const auto missing = uses_of('d').value_or(ContentUses::Uses());

    EXPECT_EQ(stored.size, size);
    EXPECT_EQ(stored.last_stored, start);
    EXPECT_EQ(stored.last_requested, start);
    EXPECT_EQ(stored.requests, 2);
    EXPECT_FALSE(missing.size.has_value());
    EXPECT_EQ(missing.last_requested, start);
    EXPECT_EQ(missing.requests, 1);
    EXPECT_FALSE(uses_of('e').has_value());
}

TEST_F(StartedRecorderTest, ContentDeletedIsRecorded) {
    Channel::post(DataStored::name, DataStored{content('c'), std::nullopt, stored_size});
    Channel::post(DataDeleted::name, DataDeleted{content('c'), stored_size});
    Channel::post(ConnectionOpened::name, ConnectionOpened{node('a'), std::string(first)});
    (void)next_nodes();
    stop_recording();

    const auto deleted = uses_of('c').value_or(ContentUses::Uses());

    EXPECT_FALSE(deleted.size.has_value());
    EXPECT_EQ(deleted.last_stored, start);
}

// Small content never asked for scores highest, then content of 1 MiB,
// whose size counts for a tenth, then content asked for most, which counts
// for a hundredth (eviction::Score).
TEST_F(StartedRecorderTest, WhatIsHeldIsListedInTheOrderToLetItGo) {
    store_to_rank();

    EXPECT_EQ(answer_to({largest_size * 2, {}}),
              (Texts{listed('b', stored_size), listed('c', stored_size),
                     listed('d', largest_size), listed('e', stored_size)}));
}

TEST_F(StartedRecorderTest, OnlyAsMuchIsListedAsFreesTheBytesAskedFor) {
    store_to_rank();

    EXPECT_EQ(answer_to({stored_size + 1, {}}),
              (Texts{listed('b', stored_size), listed('c', stored_size)}));
    EXPECT_EQ(answer_to({1, {content('b'), content('c')}}), Texts{listed('d', largest_size)});
    EXPECT_TRUE(answer_to({1, {content('b'), content('c'), content('d'), content('e')}}).empty());
}

TEST_F(RecorderTest, NoMoreAreListedThanMayBe) {
    auto settings = test_settings();

    settings.max_candidates = 2;
    start_recording(settings);
    store_to_rank();

    EXPECT_EQ(answer_to({largest_size * 2, {}}),
              (Texts{listed('b', stored_size), listed('c', stored_size)}));
}

TEST_F(StartedRecorderTest, KeepsRecordingAfterAnEventItCannotRecord) {
    Channel::post(ConnectionOpened::name, ConnectionOpened{node('a'), "not an endpoint"});
    Channel::post(ConnectionOpened::name, ConnectionOpened{node('b'), std::string(third)});

    const auto nodes = next_nodes();

    EXPECT_EQ(entries_of(nodes.added), std::vector<Named>{named(third, 'b')});
}

TEST_F(StartedRecorderTest, AnEventOfAnotherKindUnderItsNamesIsLetGo) {
    Channel::post(NodesReceived::name, std::string("not a node list"));
    Channel::post(ConnectionOpened::name, ConnectionOpened{node('a'), std::string(first)});

    const auto nodes = next_nodes();

    EXPECT_EQ(entries_of(nodes.added), std::vector<Named>{named(first, 'a')});
}

TEST_F(StartedRecorderTest, ContentNotFoundIsSoughtAtOnce) {
    Channel::post(DataNotFound::name, DataNotFound{content('a')});

    const auto seek = next_seek();

    EXPECT_EQ(texts_of(seek.seek.data()), Texts{content('a').to_string()});
    EXPECT_TRUE(seek.seek.search().empty());
}

TEST_F(StartedRecorderTest, ASearchIsSoughtAtOnce) {
    Channel::post(SearchRequested::name, SearchRequested{HashPrefix::parse("0123")});

    const auto seek = next_seek();

    EXPECT_TRUE(seek.seek.data().empty());
    EXPECT_EQ(texts_of(seek.seek.search()), Texts{"0123"});
}

TEST_F(StartedRecorderTest, WhatIsSoughtAgainIsNotPostedAgain) {
    Channel::post(DataNotFound::name, DataNotFound{content('a')});
    (void)next_seek();
    Channel::post(DataNotFound::name, DataNotFound{content('a')});

    EXPECT_TRUE(quiet());
}

TEST_F(StartedRecorderTest, ContentStoredIsNoLongerSought) {
    Channel::post(DataNotFound::name, DataNotFound{content('a')});
    Channel::post(DataNotFound::name, DataNotFound{content('b')});
    (void)next_seek();
    (void)next_seek();
    Channel::post(DataStored::name, DataStored{content('a'), node('c'), stored_size});

    const auto seek = next_seek();

    EXPECT_EQ(texts_of(seek.seek.data()), Texts{content('b').to_string()});
}

TEST_F(StartedRecorderTest, ContentStoredThatWasNotSoughtChangesNothing) {
    Channel::post(DataStored::name, DataStored{content('a'), std::nullopt, stored_size});

    EXPECT_TRUE(quiet());
}

TEST_F(StartedRecorderTest, AnEntryIsNoLongerSoughtOnceItsTtlHasPassed) {
    Channel::post(DataNotFound::name, DataNotFound{content('a')});
    Channel::post(SearchRequested::name, SearchRequested{HashPrefix::parse("0123")});
    (void)next_seek();
    (void)next_seek();
    advance(seek_entry_ttl);

    const auto seek = next_seek();

    EXPECT_TRUE(seek.seek.data().empty());
    EXPECT_TRUE(seek.seek.search().empty());
}

TEST_F(StartedRecorderTest, WhatIsSoughtIsRememberedAfterARestart) {
    Channel::post(DataNotFound::name, DataNotFound{content('a')});
    (void)next_seek();
    start_recording();

    const auto seek = next_seek();

    EXPECT_EQ(texts_of(seek.seek.data()), Texts{content('a').to_string()});
}

TEST(RecorderSettingsTest, WhatItCannotUseIsRefused) {
    const ScratchDirectory directory;
    const std::vector<std::string> own_endpoints{std::string(own_endpoint)};

    EXPECT_FALSE(refused(directory, own_endpoints, test_settings()));
    EXPECT_TRUE(refused(directory, {"not an endpoint"}, test_settings()));
    EXPECT_TRUE(refused(directory, own_endpoints, {0ms, {}}));
    EXPECT_TRUE(refused(directory, own_endpoints, {-1ms, {}}));
    EXPECT_TRUE(refused(directory, own_endpoints, {derive_interval, {}, 0ms}));
    EXPECT_TRUE(refused(directory, own_endpoints, {derive_interval, {}, -1ms}));
    EXPECT_TRUE(refused(directory, own_endpoints, test_settings({0, 1})));
    EXPECT_TRUE(refused(directory, own_endpoints, test_settings({1, 0})));
    EXPECT_TRUE(refused(directory, own_endpoints, {derive_interval, {}, seek_entry_ttl, 0}));
}

TEST(RecorderSettingsTest, ADatabaseThatCannotBeOpenedIsRefused) {
    const ScratchDirectory directory;
    const auto missing = directory.database().parent_path() / "missing" / "libranet.sqlite3";

    EXPECT_THROW((void)Recorder(missing, own_id(), {std::string(own_endpoint)}, test_settings()),
                 SqliteError);
}

TEST(RecorderEventTest, EachEventSaysWhatItHolds) {
    const auto list = list_of({{first, 'a'}, {second, 'b'}});
    const std::map<std::string, Source> sources{{std::string(first), Source::Observed}};
    const std::vector<Candidate> candidates{{node('a'), {std::string(first)}}};

    EXPECT_EQ(std::string(NodesReceived{list, sources}), "2 addresses, 1 not relayed");
    EXPECT_EQ(std::string(ConnectionOpened{node('a'), std::string(first)}),
              node('a').to_string() + " reached at " + std::string(first));
    EXPECT_EQ(std::string(ConnectionFailed{node('a'), std::string(first)}),
              node('a').to_string() + " not reached at " + std::string(first));
    EXPECT_EQ(std::string(NodesUpdated{list, list_of({{second, 'b'}})}), "2 entries, 1 new");
    EXPECT_EQ(std::string(CandidatesUpdated{candidates}), "1 candidates");
    EXPECT_EQ(std::string(SearchRequested{HashPrefix::parse("0123")}), "0123");
    EXPECT_EQ(std::string(SearchRequested{HashPrefix::parse("0123"), node('a'), 2}),
              "0123 from " + node('a').to_string() + ", to be passed on 2 more times");
    EXPECT_EQ(std::string(SeekUpdated{SeekList({node('a'), node('b')}, {})}),
              "2 identifiers, 0 prefixes");
}
