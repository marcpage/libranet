#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <cstddef>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "db/Database.hpp"
#include "peer/Buckets.hpp"
#include "peer/DialPlan.hpp"
#include "protocol/NodeList.hpp"
#include "stats/NodeAddresses.hpp"
#include "storage/Identifier.hpp"

using db::Database;
using peer::Buckets;
using peer::DialPlan;
using protocol::NodeList;
using stats::NodeAddresses;
using storage::Identifier;
using namespace std::chrono_literals;

namespace {

using Candidate = DialPlan::Candidate;

// An endpoint, at an address set aside for documentation (RFC 5737).
constexpr std::string_view endpoint = "http://203.0.113.1:4300";

// Another, for a second address in one test.
constexpr std::string_view other_endpoint = "http://203.0.113.2:4300";

// SQLite keeps a database of this name in memory, for as long as it is
// open.
constexpr std::string_view in_memory = ":memory:";

// The node id whose hash starts with digits, and is zeros after them.
Identifier node_of(const std::string& digits) {
    return {"sha256", digits + std::string(Identifier::longest_hash_digits() - digits.size(), '0')};
}

// The node ids whose hashes start with each of digits, in order.
std::vector<Identifier> nodes_of(const std::vector<std::string>& digits) {
    std::vector<Identifier> nodes;

    nodes.reserve(digits.size());
    std::transform(digits.begin(), digits.end(), std::back_inserter(nodes), node_of);
    return nodes;
}

// A candidate for each of digits, in order, each at endpoint.
std::vector<Candidate> candidates_of(const std::vector<std::string>& digits) {
    const auto nodes = nodes_of(digits);
    std::vector<Candidate> candidates;

    candidates.reserve(nodes.size());
    std::transform(nodes.begin(), nodes.end(), std::back_inserter(candidates),
                   [](const Identifier& node_id) {
        return Candidate{node_id, {std::string(endpoint)}};
    });
    return candidates;
}

// The node id, as text, of each of candidates, in order.
std::vector<std::string> ids_of(const std::vector<Candidate>& candidates) {
    std::vector<std::string> node_ids;

    node_ids.reserve(candidates.size());
    std::transform(candidates.begin(), candidates.end(), std::back_inserter(node_ids),
                   [](const Candidate& candidate) { return candidate.node_id.to_string(); });
    return node_ids;
}

// The node id, as text, of the node of each of digits, in order.
std::vector<std::string> ids_named(const std::vector<std::string>& digits) {
    return ids_of(candidates_of(digits));
}

// A clock that stands still until it is moved on.
class StoppedClock {
public:
    [[nodiscard]] DialPlan::Now now() const;
    void advance(DialPlan::Clock::duration duration);

private:
    DialPlan::Clock::time_point m_now;
};

DialPlan::Now StoppedClock::now() const {
    return [this] { return m_now; };
}

void StoppedClock::advance(DialPlan::Clock::duration duration) {
    m_now += duration;
}

// The plan of the node a0, with sizes and the default rests, at the time
// clock says.
DialPlan plan_of(const StoppedClock& clock,
                 const Buckets::ExpectedSizes& sizes = Buckets::ExpectedSizes{}) {
    return DialPlan(node_of("a0"), sizes, DialPlan::Rests(), clock.now());
}

// Whether DialPlan refuses sizes, rests, and now as invalid.
bool refused(const Buckets::ExpectedSizes& sizes, const DialPlan::Rests& rests,
             const DialPlan::Now& now) {
    try {
        (void)DialPlan(node_of("a0"), sizes, rests, now);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

} // namespace

TEST(DialPlanTest, DefaultsAreThePythonNodes) {
    const DialPlan::Rests rests;

    EXPECT_EQ(rests.retry_delay, 60s);
    EXPECT_EQ(rests.turned_away_rest, 3600s);
}

TEST(DialPlanTest, TheFirstCandidateInEachEmptyBucketIsDialed) {
    const StoppedClock clock;
    const auto plan = plan_of(clock);

    const auto chosen = plan.to_dial(candidates_of({"10", "11", "3f", "1f", "f0"}), {});

    EXPECT_EQ(ids_of(chosen), ids_named({"10", "3f", "f0"}));
    ASSERT_FALSE(chosen.empty());
    EXPECT_EQ(chosen.front().endpoints, std::vector<std::string>{std::string(endpoint)});
}

TEST(DialPlanTest, NoCandidatesDialsNone) {
    const StoppedClock clock;
    const auto plan = plan_of(clock);

    EXPECT_TRUE(plan.to_dial({}, {}).empty());
}

TEST(DialPlanTest, CandidatesInFilledBucketsAreNotDialed) {
    const StoppedClock clock;
    const auto plan = plan_of(clock);

    const auto chosen = plan.to_dial(candidates_of({"11", "3f", "30"}), nodes_of({"10", "30"}));

    EXPECT_TRUE(ids_of(chosen).empty());
}

// The first in this node's own primary bucket, a5, fills it. Those after it
// fill the secondary set: a6 its bucket 6, and a51 its bucket 5, as a5 counts
// there only once it is connected.
TEST(DialPlanTest, NeighborsFillThisNodesPrimaryBucketThenTheSecondarySet) {
    const StoppedClock clock;
    const auto plan = plan_of(clock);

    const auto chosen = plan.to_dial(candidates_of({"a5", "a6", "a51", "a52"}), {});

    EXPECT_EQ(ids_of(chosen), ids_named({"a5", "a6", "a51"}));
}

TEST(DialPlanTest, ServerNodesAndThisNodeAreNotDialed) {
    const StoppedClock clock;
    const auto plan = plan_of(clock);

    const auto chosen = plan.to_dial(candidates_of({"a0", "a5", "a6"}), nodes_of({"a5"}));

    EXPECT_EQ(ids_of(chosen), ids_named({"a6"}));
}

TEST(DialPlanTest, ASetWithItsServerNodesDialsNoMore) {
    constexpr std::size_t server_nodes = 2;
    const StoppedClock clock;
    Buckets::ExpectedSizes sizes;

    sizes.min_primary_connections = server_nodes;
    sizes.min_secondary_connections = 0;

    const auto plan = plan_of(clock, sizes);

    EXPECT_EQ(ids_of(plan.to_dial(candidates_of({"00", "10", "20"}), {})), ids_named({"00", "10"}));
    EXPECT_TRUE(plan.to_dial(candidates_of({"20"}), nodes_of({"00", "10"})).empty());
}

TEST(DialPlanTest, AFailedNodeRestsForTheRetryDelay) {
    StoppedClock clock;
    auto plan = plan_of(clock);
    const auto candidates = candidates_of({"10"});

    plan.rest_failed(node_of("10"));
    clock.advance(DialPlan::Rests::default_retry_delay - 1ms);

    EXPECT_TRUE(plan.to_dial(candidates, {}).empty());

    clock.advance(1ms);

    EXPECT_EQ(ids_of(plan.to_dial(candidates, {})), ids_named({"10"}));
}

TEST(DialPlanTest, ANodeThatTurnedThisNodeAwayRestsLonger) {
    StoppedClock clock;
    auto plan = plan_of(clock);
    const auto candidates = candidates_of({"10"});

    plan.rest_turned_away(node_of("10"));
    clock.advance(DialPlan::Rests::default_retry_delay);

    EXPECT_TRUE(plan.to_dial(candidates, {}).empty());

    clock.advance(DialPlan::Rests::default_turned_away_rest - DialPlan::Rests::default_retry_delay
                  - 1ms);

    EXPECT_TRUE(plan.to_dial(candidates, {}).empty());

    clock.advance(1ms);

    EXPECT_EQ(ids_of(plan.to_dial(candidates, {})), ids_named({"10"}));
}

TEST(DialPlanTest, AFailureDoesNotShortenARest) {
    StoppedClock clock;
    auto plan = plan_of(clock);
    const auto candidates = candidates_of({"10"});

    plan.rest_turned_away(node_of("10"));
    plan.rest_failed(node_of("10"));
    clock.advance(DialPlan::Rests::default_turned_away_rest - 1ms);

    EXPECT_TRUE(plan.to_dial(candidates, {}).empty());

    clock.advance(1ms);

    EXPECT_EQ(ids_of(plan.to_dial(candidates, {})), ids_named({"10"}));
}

TEST(DialPlanTest, WhileANodeRestsTheNextInItsBucketIsDialed) {
    const StoppedClock clock;
    auto plan = plan_of(clock);

    plan.rest_turned_away(node_of("10"));
    plan.rest_failed(node_of("a5"));

    const auto chosen = plan.to_dial(candidates_of({"10", "a5", "11", "a6"}), {});

    EXPECT_EQ(ids_of(chosen), ids_named({"11", "a6"}));
}

// NodeAddresses gives the nodes reached before first, so the node reached at
// 1f is dialed for bucket 1 ahead of 11, which was learned of later and
// never reached.
TEST(DialPlanTest, ANodeReachedBeforeIsDialedAheadOfOneNeverReached) {
    Database database(std::string(in_memory), NodeAddresses::schema);
    NodeAddresses addresses(database, NodeAddresses::Limits{});
    NodeList learned;
    const StoppedClock clock;
    const auto plan = plan_of(clock);

    learned.add(std::string(other_endpoint), node_of("11"));
    addresses.record_success(node_of("1f"), endpoint);
    addresses.record_learned(learned, NodeAddresses::Source::Advertised);

    const auto chosen = plan.to_dial(addresses.candidates(node_of("a0")), {});

    EXPECT_EQ(ids_of(chosen), ids_named({"1f"}));
}

TEST(DialPlanTest, RestsOutOfRangeAndNoClockAreRefused) {
    const StoppedClock clock;
    DialPlan::Rests no_retry_delay;
    DialPlan::Rests negative_turned_away_rest;
    Buckets::ExpectedSizes no_primary;

    no_retry_delay.retry_delay = 0ms;
    negative_turned_away_rest.turned_away_rest = -1ms;
    no_primary.min_primary_connections = 0;

    EXPECT_TRUE(refused(Buckets::ExpectedSizes{}, no_retry_delay, clock.now()));
    EXPECT_TRUE(refused(Buckets::ExpectedSizes{}, negative_turned_away_rest, clock.now()));
    EXPECT_TRUE(refused(no_primary, DialPlan::Rests{}, clock.now()));
    EXPECT_TRUE(refused(Buckets::ExpectedSizes{}, DialPlan::Rests{}, DialPlan::Now{}));
    EXPECT_FALSE(refused(Buckets::ExpectedSizes{}, DialPlan::Rests{}, clock.now()));
}

TEST(DialPlanTest, ANodeIdWithNoHashIsRefused) {
    const StoppedClock clock;
    const auto plan = plan_of(clock);

    EXPECT_THROW((void)DialPlan(Identifier(), Buckets::ExpectedSizes{}, DialPlan::Rests{},
                                clock.now()),
                 std::invalid_argument);
    EXPECT_THROW((void)plan.to_dial({{Identifier(), {std::string(endpoint)}}}, {}),
                 std::invalid_argument);
    EXPECT_THROW((void)plan.to_dial({}, {Identifier()}), std::invalid_argument);
}
