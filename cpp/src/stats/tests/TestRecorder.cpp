#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <cstddef>
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
#include "msg/Channel.hpp"
#include "msg/Message.hpp"
#include "protocol/NodeList.hpp"
#include "stats/ContentUses.hpp"
#include "stats/NodeAddresses.hpp"
#include "stats/Recorder.hpp"
#include "storage/DataRequested.hpp"
#include "storage/DataStored.hpp"
#include "storage/Identifier.hpp"

using db::Database;
using db::SqliteError;
using fixtures::ScratchDirectory;
using msg::Channel;
using protocol::NodeList;
using stats::ContentUses;
using stats::NodeAddresses;
using stats::Recorder;
using storage::DataRequested;
using storage::DataStored;
using storage::Identifier;
using namespace std::chrono_literals;

// The tests only take the lists the recorder posts, from another file, so
// nothing here would make their messages' virtual to_string(), which
// next<>() casts to.
template class msg::Message<Recorder::NodesUpdated>;
template class msg::Message<Recorder::CandidatesUpdated>;

namespace {

using Candidate = NodeAddresses::Candidate;
using CandidatesUpdated = Recorder::CandidatesUpdated;
using Clock = Recorder::Clock;
using ConnectionFailed = Recorder::ConnectionFailed;
using ConnectionOpened = Recorder::ConnectionOpened;
using NodesReceived = Recorder::NodesReceived;
using NodesUpdated = Recorder::NodesUpdated;
using Settings = Recorder::Settings;
using Source = NodeAddresses::Source;
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

// When the tests' clock stands, in whole seconds, as the table keeps times
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

// The settings the tests derive with, and limits.
Settings test_settings(NodeAddresses::Limits limits = {}) {
    return {derive_interval, limits};
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
// still, and channels that take the lists it posts, made before it so that
// they take the first.
class RecorderTest : public testing::Test {
protected:
    // Starts the recorder, again if it was running, with settings.
    void start_recording(Settings new_settings = test_settings());
    // Stops the recorder, which closes the database.
    void stop_recording() noexcept;

    // The next node list or candidates posted. Throws std::runtime_error if
    // none is posted in time.
    [[nodiscard]] NodesUpdated next_nodes();
    [[nodiscard]] CandidatesUpdated next_candidates();
    // Whether neither list is posted for a while.
    [[nodiscard]] bool quiet();
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
    std::unique_ptr<Recorder> m_recorder;
};

void RecorderTest::start_recording(Settings new_settings) {
    stop_recording();
    m_recorder = std::make_unique<Recorder>(m_directory.database(), own_id(),
                                            std::vector<std::string>{std::string(own_endpoint)},
                                            new_settings, [] { return start; });
}

void RecorderTest::stop_recording() noexcept {
    m_recorder.reset();
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

bool RecorderTest::quiet() {
    const auto nodes = m_nodes.next<NodesUpdated>(quiet_time);
    const auto candidates = m_candidates.next<CandidatesUpdated>(quiet_time);

    return !nodes && !candidates;
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
}

} // namespace

TEST_F(RecorderTest, PostsTheListsAsItStarts) {
    start_recording();

    const auto nodes = next_nodes();
    const auto candidates = next_candidates();

    EXPECT_EQ(entries_of(nodes.nodes), std::vector<Named>{named(own_endpoint, '0')});
    EXPECT_TRUE(nodes.added.entries().empty());
    EXPECT_TRUE(candidates.candidates.empty());
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

TEST(RecorderSettingsTest, WhatItCannotUseIsRefused) {
    const ScratchDirectory directory;
    const std::vector<std::string> own_endpoints{std::string(own_endpoint)};

    EXPECT_FALSE(refused(directory, own_endpoints, test_settings()));
    EXPECT_TRUE(refused(directory, {"not an endpoint"}, test_settings()));
    EXPECT_TRUE(refused(directory, own_endpoints, {0ms, {}}));
    EXPECT_TRUE(refused(directory, own_endpoints, {-1ms, {}}));
    EXPECT_TRUE(refused(directory, own_endpoints, test_settings({0, 1})));
    EXPECT_TRUE(refused(directory, own_endpoints, test_settings({1, 0})));
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
}
