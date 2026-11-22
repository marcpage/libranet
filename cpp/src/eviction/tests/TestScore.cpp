#include <gtest/gtest.h>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "eviction/Score.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"

using eviction::Score;
using storage::HashPrefix;
using storage::Identifier;
using namespace std::chrono_literals;

namespace {

using Clock = Score::Clock;

// The hash of the tests' node id, which is all content is matched against.
constexpr std::string_view node_hash =
    "6b86b273ff34fce19d6b804eff5a3f5747ada4eaa22f1d49c01e52ddb7875b4b";

// When the tests score content.
constexpr auto now = Clock::time_point(1'000'000s);

// Scored against a node whose content held went unused 100 s at most, was
// asked for 10 times at most, and matches the node id by 16 bits at most.
constexpr auto longest_unused = 100s;
constexpr std::int64_t most_requests = 10;
constexpr std::size_t most_bits = 16;

// A hex digit is four bits, the top one first.
constexpr std::size_t bits_per_digit = 4;
constexpr unsigned top_bit = 0x8U;

// The largest an object may be (HighLevelDesign §4.3), and twice that.
constexpr std::size_t largest = types::Megabyte;
constexpr std::size_t twice_largest = 2 * types::Megabyte;

// A small object, as against one of 1 MiB.
constexpr std::size_t small = 1'000;

// Asked for now and then, often, or more than any object held.
constexpr std::int64_t some_requests = 3;
constexpr std::int64_t many_requests = 9;
constexpr std::int64_t more_than_most_requests = most_requests + 5;

// Unused half as long as the longest, and used a minute after now.
constexpr auto half_unused = 50s;
constexpr auto after_now = 60s;

// The bits a node's own key matches of its id, but for the last.
constexpr std::size_t all_but_one_bit = 255;

// How near a score is to the one expected: within this fraction of it, or
// within this of 1.
constexpr double tolerance = 1e-5;

Identifier node_id() {
    return {"sha256", std::string(node_hash)};
}

// The identifier whose hash shares exactly bits leading bits with the node
// id's: its own, with the next bit flipped.
Identifier sharing(std::size_t bits) {
    constexpr std::string_view digits = "0123456789abcdef";
    std::string hash(node_hash);
    auto& digit = hash.at(bits / bits_per_digit);
    const auto value = static_cast<unsigned>(digits.find(digit))
                       ^ (top_bit >> (bits % bits_per_digit));

    digit = digits.at(value);
    return {"sha256", hash};
}

// What factor, a fraction of one, counts for in a score.
double floored(double factor, double floor = Score::factor_floor) {
    return floor + ((1.0 - floor) * factor);
}

Score scorer() {
    return {node_id(), now, Score::Extremes{longest_unused, most_requests, most_bits}};
}

// The score of an object of size bytes, asked for requests times, last used
// at last_used, and sharing bits with the node id.
double score_of(std::size_t size,
                std::int64_t requests,
                std::optional<Clock::time_point> last_used,
                std::size_t bits) {
    return scorer().of(sharing(bits), size, requests, last_used);
}

} // namespace

TEST(ScoreTest, SharingMakesIdsOfTheBitsAskedFor) {
    const auto node = HashPrefix::parse(node_hash);

    EXPECT_EQ(node.matching_bits(sharing(0)), 0U);
    EXPECT_EQ(node.matching_bits(sharing(3)), 3U);
    EXPECT_EQ(node.matching_bits(sharing(most_bits + 1)), most_bits + 1);
    EXPECT_EQ(node.matching_bits(sharing(all_but_one_bit)), all_but_one_bit);
}

TEST(ScoreTest, OneByteUnusedLongestNeverAskedForMatchingNothingScoresAboutOne) {
    const auto score = score_of(1, 0, now - longest_unused, 0);

    EXPECT_NEAR(score, 1.0, tolerance);
    EXPECT_LT(score, 1.0);
}

TEST(ScoreTest, OneFactorAtItsWorstCountsForItsFloorNotZero) {
    const auto floor = Score::factor_floor;

    // Used just now
    EXPECT_NEAR(score_of(1, 0, now, 0), floor, floor * tolerance);
    // Asked for the most
    EXPECT_NEAR(score_of(1, most_requests, now - longest_unused, 0), floor, floor * tolerance);
    // As large as an object may be
    EXPECT_NEAR(score_of(largest, 0, now - longest_unused, 0),
                Score::size_factor_floor,
                Score::size_factor_floor * tolerance);
    // The best match held
    EXPECT_NEAR(score_of(1, 0, now - longest_unused, most_bits), floor, floor * tolerance);
}

TEST(ScoreTest, EveryFactorAtItsWorstIsTheProductOfTheFloors) {
    const auto floors = Score::factor_floor * Score::factor_floor * Score::factor_floor
                        * Score::size_factor_floor;

    EXPECT_NEAR(score_of(largest, most_requests, now, most_bits), floors, floors * tolerance);
}

TEST(ScoreTest, EachFactorIsAFractionOfTheExtremeHeld) {
    const auto unused = floored(0.25);
    const auto asked_for = floored(0.5);
    const auto size = floored(0.75, Score::size_factor_floor);
    const auto bits = floored(0.75);

    EXPECT_NEAR(score_of(1, 0, now - (longest_unused / 4), 0), unused, unused * tolerance);
    EXPECT_NEAR(score_of(1, most_requests / 2, now - longest_unused, 0),
                asked_for,
                asked_for * tolerance);
    EXPECT_NEAR(score_of(largest / 4, 0, now - longest_unused, 0), size, size * tolerance);
    EXPECT_NEAR(score_of(1, 0, now - longest_unused, most_bits / 4), bits, bits * tolerance);
}

TEST(ScoreTest, AnExtremeOfZeroLeavesItsFactorAtOne) {
    // Nothing held was ever asked for, or unused for any time, or matches a
    // bit.
    const Score score(node_id(), now, Score::Extremes{});

    EXPECT_NEAR(score.of(sharing(0), 1, 0, now), 1.0, tolerance);
}

TEST(ScoreTest, WhenLastUsedIsUnknownItCountsAsUnusedLongest) {
    EXPECT_NEAR(score_of(1, 0, std::nullopt, 0), 1.0, tolerance);
}

TEST(ScoreTest, AFactorBeyondItsExtremeIsKeptWithinItsRange) {
    const auto floor = Score::factor_floor;

    // Used after now, as the clock sees it
    EXPECT_NEAR(score_of(1, 0, now + after_now, 0), floor, floor * tolerance);
    EXPECT_NEAR(score_of(1, more_than_most_requests, now - longest_unused, 0),
                floor,
                floor * tolerance);
    EXPECT_NEAR(score_of(twice_largest, 0, now - longest_unused, 0),
                Score::size_factor_floor,
                Score::size_factor_floor * tolerance);
    // This node's own key matches every bit
    EXPECT_NEAR(score_of(1, 0, now - longest_unused, all_but_one_bit), floor, floor * tolerance);
}

TEST(ScoreTest, AnUnusedObjectOfOneMibGoesBeforeABusyOne) {
    const auto unused = score_of(largest, 0, now - longest_unused, 2);
    const auto busy = score_of(largest, many_requests, now - 1s, 0);

    EXPECT_GT(unused, busy);
    EXPECT_GT(busy, 0.0);
}

TEST(ScoreTest, ASmallObjectInUseGoesAfterALargeOneUnused) {
    const auto in_use = score_of(small, some_requests, now - 1s, 0);
    const auto large_unused = score_of(largest, 0, now - longest_unused, 0);

    EXPECT_LT(in_use, large_unused);
}

TEST(ScoreTest, ASmallObjectNoLongerInUseGoesBeforeALargeOne) {
    const auto small_unused = score_of(small, some_requests, now - longest_unused, 0);
    const auto large_unused = score_of(largest, some_requests, now - longest_unused, 0);

    EXPECT_GT(small_unused, large_unused);
}

TEST(ScoreTest, MatchingMoreBitsKeepsContentLonger) {
    std::vector<double> scores;

    for (std::size_t bits = 0; bits < most_bits; bits += bits_per_digit) {
        scores.push_back(score_of(small, some_requests, now - half_unused, bits));
    }

    for (std::size_t index = 1; index < scores.size(); ++index) {
        EXPECT_GT(scores.at(index - 1), scores.at(index));
    }
}

TEST(ScoreTest, ANodeIdWithNoHashIsRefused) {
    EXPECT_THROW((void)Score(Identifier(), now, Score::Extremes{}), std::invalid_argument);
}
