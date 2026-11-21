#include <gtest/gtest.h>
#include <algorithm>
#include <cstddef>
#include <iterator>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

#include "peer/Buckets.hpp"
#include "storage/Identifier.hpp"

using peer::Buckets;
using storage::Identifier;

namespace {

// How many buckets each set has with the default sizes: one for each value
// of four bits.
constexpr std::size_t default_buckets = 16;

// One more than the most prefix bits, and than the connections four bits
// have buckets for.
constexpr std::size_t too_many_bits = 17;
constexpr std::size_t too_many_connections = 17;

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

// Every bucket of a set of the default sizes, lowest first, but filled.
std::vector<std::size_t> every_bucket_but(const std::set<std::size_t>& filled) {
    std::vector<std::size_t> buckets;

    for (std::size_t bucket = 0; bucket < default_buckets; ++bucket) {
        if (0 == filled.count(bucket)) {
            buckets.push_back(bucket);
        }
    }

    return buckets;
}

// Whether Buckets refuses sizes as invalid.
bool refused(const Buckets::Sizes& sizes) {
    try {
        (void)Buckets(node_of("a0"), sizes);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

} // namespace

TEST(BucketsTest, DefaultsAreThePythonNodes) {
    const Buckets::Sizes sizes;

    EXPECT_EQ(sizes.min_outgoing_connections, 16U);
    EXPECT_EQ(sizes.bucket_prefix_bits, 4U);
    EXPECT_EQ(sizes.min_neighborhood_connections, 16U);
    EXPECT_EQ(sizes.neighborhood_prefix_bits, 4U);
}

TEST(BucketsTest, BucketIsTheFirstBits) {
    const Buckets buckets(node_of("a0"), Buckets::Sizes{});

    EXPECT_EQ(buckets.bucket(node_of("00")), 0U);
    EXPECT_EQ(buckets.bucket(node_of("3f")), 3U);
    EXPECT_EQ(buckets.bucket(node_of("a5")), 10U);
    EXPECT_EQ(buckets.bucket(node_of("ff")), 15U);
}

TEST(BucketsTest, NeighborhoodBucketIsTheBitsAfter) {
    const Buckets buckets(node_of("a5"), Buckets::Sizes{});

    EXPECT_EQ(buckets.neighborhood_bucket(node_of("a5")), 5U);
    EXPECT_EQ(buckets.neighborhood_bucket(node_of("a0")), 0U);
    EXPECT_EQ(buckets.neighborhood_bucket(node_of("a7f")), 7U);
    EXPECT_EQ(buckets.neighborhood_bucket(node_of("af")), 15U);
}

TEST(BucketsTest, NodesOutsideThisNodesBucketHaveNoNeighborhoodBucket) {
    const Buckets buckets(node_of("a5"), Buckets::Sizes{});

    EXPECT_FALSE(buckets.neighborhood_bucket(node_of("b5")).has_value());
    EXPECT_FALSE(buckets.neighborhood_bucket(node_of("05")).has_value());
    EXPECT_FALSE(buckets.neighborhood_bucket(node_of("5a")).has_value());
}

// 0xb8 is 1011 1000: its first three bits are 5, as 0xa's are, and the two
// after them are 3.
TEST(BucketsTest, BitsNeedNotFillADigit) {
    constexpr std::size_t bucket_bits = 3;
    constexpr std::size_t neighborhood_bits = 2;
    Buckets::Sizes sizes;

    sizes.min_outgoing_connections = 1U << bucket_bits;
    sizes.bucket_prefix_bits = bucket_bits;
    sizes.min_neighborhood_connections = 1U << neighborhood_bits;
    sizes.neighborhood_prefix_bits = neighborhood_bits;

    const Buckets buckets(node_of("a0"), sizes);

    EXPECT_EQ(buckets.bucket(node_of("b8")), 5U);
    EXPECT_EQ(buckets.bucket(node_of("c0")), 6U);
    EXPECT_EQ(buckets.neighborhood_bucket(node_of("b8")), 3U);
    EXPECT_EQ(buckets.neighborhood_bucket(node_of("a0")), 0U);
    EXPECT_FALSE(buckets.neighborhood_bucket(node_of("c0")).has_value());
}

TEST(BucketsTest, TheWidestBucketsReadEightDigits) {
    constexpr std::size_t widest = 16;
    Buckets::Sizes sizes;

    sizes.bucket_prefix_bits = widest;
    sizes.neighborhood_prefix_bits = widest;

    const Buckets buckets(node_of("fffe0000"), sizes);

    EXPECT_EQ(buckets.bucket(node_of("fffe1234")), 65'534U);
    EXPECT_EQ(buckets.neighborhood_bucket(node_of("fffe1234")), 4'660U);
    EXPECT_EQ(buckets.neighborhood_bucket(node_of("fffeffff")), 65'535U);
    EXPECT_FALSE(buckets.neighborhood_bucket(node_of("ffff0000")).has_value());
}

TEST(BucketsTest, NoConnectionsLeaveEveryBucketEmpty) {
    const Buckets buckets(node_of("a0"), Buckets::Sizes{});

    const auto gaps = buckets.gaps({});

    EXPECT_EQ(gaps.outgoing.buckets, every_bucket_but({}));
    EXPECT_EQ(gaps.outgoing.wanted, 16U);
    EXPECT_EQ(gaps.neighborhood.buckets, every_bucket_but({}));
    EXPECT_EQ(gaps.neighborhood.wanted, 16U);
}

// The neighbor fills its bucket of the neighborhood, so this node's own
// bucket of the first set, 10, is still empty.
TEST(BucketsTest, ConnectionsFillTheirBuckets) {
    const Buckets buckets(node_of("a0"), Buckets::Sizes{});

    const auto gaps = buckets.gaps(nodes_of({"10", "3f", "a5"}));

    EXPECT_EQ(gaps.outgoing.buckets, every_bucket_but({1, 3}));
    EXPECT_EQ(gaps.outgoing.wanted, 14U);
    EXPECT_EQ(gaps.neighborhood.buckets, every_bucket_but({5}));
    EXPECT_EQ(gaps.neighborhood.wanted, 15U);
}

TEST(BucketsTest, ConnectionsInOneBucketFillItOnce) {
    const Buckets buckets(node_of("a0"), Buckets::Sizes{});

    const auto gaps = buckets.gaps(nodes_of({"10", "11", "1f"}));

    EXPECT_EQ(gaps.outgoing.buckets, every_bucket_but({1}));
    EXPECT_EQ(gaps.outgoing.wanted, 15U);
    EXPECT_EQ(gaps.neighborhood.wanted, 16U);
}

TEST(BucketsTest, ASecondNeighborInABucketFillsThisNodesOwnBucket) {
    const Buckets buckets(node_of("a0"), Buckets::Sizes{});

    const auto gaps = buckets.gaps(nodes_of({"a51", "a52"}));

    EXPECT_EQ(gaps.outgoing.buckets, every_bucket_but({10}));
    EXPECT_EQ(gaps.outgoing.wanted, 15U);
    EXPECT_EQ(gaps.neighborhood.buckets, every_bucket_but({5}));
    EXPECT_EQ(gaps.neighborhood.wanted, 15U);
}

TEST(BucketsTest, NeighborsPastTheNeighborhoodFillThisNodesOwnBucket) {
    Buckets::Sizes sizes;

    sizes.min_neighborhood_connections = 1;

    const Buckets buckets(node_of("a0"), sizes);

    const auto gaps = buckets.gaps(nodes_of({"a1", "a2"}));

    EXPECT_EQ(gaps.outgoing.buckets, every_bucket_but({10}));
    EXPECT_EQ(gaps.outgoing.wanted, 15U);
    EXPECT_EQ(gaps.neighborhood.buckets, every_bucket_but({1, 2}));
    EXPECT_EQ(gaps.neighborhood.wanted, 0U);
}

TEST(BucketsTest, WithNoNeighborhoodANeighborFillsThisNodesOwnBucket) {
    Buckets::Sizes sizes;

    sizes.min_neighborhood_connections = 0;

    const Buckets buckets(node_of("a0"), sizes);

    const auto gaps = buckets.gaps(nodes_of({"a1"}));

    EXPECT_EQ(gaps.outgoing.buckets, every_bucket_but({10}));
    EXPECT_EQ(gaps.outgoing.wanted, 15U);
    EXPECT_EQ(gaps.neighborhood.wanted, 0U);
}

// A node named twice is one neighbor, so it does not fill this node's own
// bucket of the first set.
TEST(BucketsTest, ANodeConnectedTwiceFillsOneBucket) {
    const Buckets buckets(node_of("a0"), Buckets::Sizes{});

    const auto gaps = buckets.gaps(nodes_of({"a5", "10", "a5", "10"}));

    EXPECT_EQ(gaps.outgoing.buckets, every_bucket_but({1}));
    EXPECT_EQ(gaps.outgoing.wanted, 15U);
    EXPECT_EQ(gaps.neighborhood.buckets, every_bucket_but({5}));
    EXPECT_EQ(gaps.neighborhood.wanted, 15U);
}

// A set meant to have fewer connections than it has buckets wants none
// once it has them, though it still has buckets empty.
TEST(BucketsTest, ASetWithItsConnectionsWantsNoMore) {
    constexpr std::size_t connections = 4;
    Buckets::Sizes sizes;

    sizes.min_outgoing_connections = connections;

    const Buckets buckets(node_of("a0"), sizes);

    const auto gaps = buckets.gaps(nodes_of({"00", "10", "20", "30"}));

    EXPECT_EQ(gaps.outgoing.buckets, every_bucket_but({0, 1, 2, 3}));
    EXPECT_EQ(gaps.outgoing.wanted, 0U);
}

// A node in each bucket of the first set but this node's own, a neighbor in
// each bucket of the neighborhood, and a second neighbor, which fills this
// node's own bucket.
TEST(BucketsTest, EveryBucketFilled) {
    const Buckets buckets(node_of("a0"), Buckets::Sizes{});
    std::vector<std::string> digits;

    for (const auto digit : std::string("0123456789abcdef")) {
        if ('a' != digit) {
            digits.push_back(std::string(1, digit) + "0");
        }

        digits.push_back(std::string("a") + digit + "1");
    }

    digits.emplace_back("a02");

    const auto gaps = buckets.gaps(nodes_of(digits));

    EXPECT_TRUE(gaps.outgoing.buckets.empty());
    EXPECT_EQ(gaps.outgoing.wanted, 0U);
    EXPECT_TRUE(gaps.neighborhood.buckets.empty());
    EXPECT_EQ(gaps.neighborhood.wanted, 0U);
}

TEST(BucketsTest, PrefixBitsOutOfRangeAreRefused) {
    Buckets::Sizes no_bits;
    Buckets::Sizes too_many;
    Buckets::Sizes no_neighborhood_bits;
    Buckets::Sizes too_many_neighborhood;

    no_bits.bucket_prefix_bits = 0;
    too_many.bucket_prefix_bits = too_many_bits;
    no_neighborhood_bits.neighborhood_prefix_bits = 0;
    too_many_neighborhood.neighborhood_prefix_bits = too_many_bits;

    EXPECT_TRUE(refused(no_bits));
    EXPECT_TRUE(refused(too_many));
    EXPECT_TRUE(refused(no_neighborhood_bits));
    EXPECT_TRUE(refused(too_many_neighborhood));
}

TEST(BucketsTest, MoreConnectionsThanBucketsAreRefused) {
    Buckets::Sizes no_outgoing;
    Buckets::Sizes too_many_outgoing;
    Buckets::Sizes too_many_neighbors;
    Buckets::Sizes no_neighbors;

    no_outgoing.min_outgoing_connections = 0;
    too_many_outgoing.min_outgoing_connections = too_many_connections;
    too_many_neighbors.min_neighborhood_connections = too_many_connections;
    no_neighbors.min_neighborhood_connections = 0;

    EXPECT_TRUE(refused(no_outgoing));
    EXPECT_TRUE(refused(too_many_outgoing));
    EXPECT_TRUE(refused(too_many_neighbors));
    EXPECT_FALSE(refused(no_neighbors));
    EXPECT_FALSE(refused(Buckets::Sizes{}));
}

TEST(BucketsTest, ANodeIdWithNoHashIsRefused) {
    const Buckets buckets(node_of("a0"), Buckets::Sizes{});

    EXPECT_THROW((void)Buckets(Identifier(), Buckets::Sizes{}), std::invalid_argument);
    EXPECT_THROW((void)buckets.bucket(Identifier()), std::invalid_argument);
    EXPECT_THROW((void)buckets.neighborhood_bucket(Identifier()), std::invalid_argument);
    EXPECT_THROW((void)buckets.gaps({Identifier()}), std::invalid_argument);
}
