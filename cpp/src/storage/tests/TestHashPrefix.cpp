#include <gtest/gtest.h>
#include <cstddef>
#include <stdexcept>
#include <string>

#include "Fixtures.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"

using fixtures::hello_hash;
using storage::HashPrefix;
using storage::Identifier;

namespace {

// Whether HashPrefix::parse() turns down text as invalid.
bool refused(const std::string& text) {
    try {
        (void)HashPrefix::parse(text);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

// How many leading bits the prefixes left and right share.
std::size_t matching_bits(const std::string& left, const std::string& right) {
    return HashPrefix::parse(left).matching_bits(HashPrefix::parse(right));
}

} // namespace

TEST(HashPrefixTest, DigitsAndBits) {
    const auto prefix = HashPrefix::parse("2cf2");

    EXPECT_EQ(prefix.to_string(), "2cf2");
    EXPECT_EQ(prefix.bits(), 16U);
}

// A prefix may be as long as a SHA-256 hash, or as short as one digit
// (HttpApi §6).
TEST(HashPrefixTest, OneDigitToAWholeHash) {
    const auto digit = HashPrefix::parse("2");
    const auto whole = HashPrefix::parse(hello_hash);

    EXPECT_EQ(digit.bits(), 4U);
    EXPECT_EQ(whole.to_string(), hello_hash);
    EXPECT_EQ(whole.bits(), 256U);
}

// Hashes should be in lower case, but upper and mixed case are taken, and
// kept in lower case (HttpApi §5.4).
TEST(HashPrefixTest, UpperOrMixedCaseIsKeptInLowerCase) {
    EXPECT_EQ(HashPrefix::parse("2CF2").to_string(), "2cf2");
    EXPECT_EQ(HashPrefix::parse("2Cf2DbA").to_string(), "2cf2dba");
}

TEST(HashPrefixTest, EmptyOrLongerThanAHashIsRefused) {
    EXPECT_TRUE(refused(""));
    EXPECT_TRUE(refused(std::string(hello_hash) + "0"));
}

TEST(HashPrefixTest, NotHexIsRefused) {
    EXPECT_TRUE(refused("g"));
    EXPECT_TRUE(refused("2cf2 "));
    EXPECT_TRUE(refused(" 2cf2"));
    EXPECT_TRUE(refused("0x2c"));
    EXPECT_TRUE(refused("2c.f"));
    EXPECT_TRUE(refused("sha256/2cf2"));
    EXPECT_TRUE(refused(std::string("2c\0f", 4)));
}

// Each digit two prefixes agree on is four bits, up to as many as the
// shorter has.
TEST(HashPrefixTest, SharedDigitsAreFourBitsEach) {
    EXPECT_EQ(matching_bits("2cf2", "2cf2"), 16U);
    EXPECT_EQ(matching_bits("2cf2", "2c"), 8U);
    EXPECT_EQ(matching_bits("2cf2", "2cf24dba"), 16U);
    EXPECT_EQ(matching_bits("2cf2", "2CF2"), 16U);
}

// Bits, not digits: two digits that differ share their top bits, down to
// the first that differs.
TEST(HashPrefixTest, DifferingDigitsShareTheirTopBits) {
    EXPECT_EQ(matching_bits("8", "9"), 3U); // 1000, 1001
    EXPECT_EQ(matching_bits("8", "a"), 2U); // 1010
    EXPECT_EQ(matching_bits("8", "c"), 1U); // 1100
    EXPECT_EQ(matching_bits("8", "7"), 0U); // 0111
    EXPECT_EQ(matching_bits("0", "f"), 0U);
    EXPECT_EQ(matching_bits("0", "1"), 3U);
    EXPECT_EQ(matching_bits("2c0", "2c1"), 11U);
    EXPECT_EQ(matching_bits("2c0f", "2c8f"), 8U);
}

TEST(HashPrefixTest, MatchingIsTheSameEitherWayRound) {
    EXPECT_EQ(matching_bits("9", "8"), 3U);
    EXPECT_EQ(matching_bits("2c", "2cf2"), 8U);
    EXPECT_EQ(matching_bits("2c1", "2c0"), 11U);
}

// The identifier's hash is compared, "2cf24dba..." for "hello".
TEST(HashPrefixTest, MatchingBitsWithAnIdentifier) {
    const Identifier identifier("sha256", hello_hash);

    EXPECT_EQ(HashPrefix::parse("2cf2").matching_bits(identifier), 16U);
    EXPECT_EQ(HashPrefix::parse("2cf3").matching_bits(identifier), 15U);
    EXPECT_EQ(HashPrefix::parse("2CF24D").matching_bits(identifier), 24U);
    EXPECT_EQ(HashPrefix::parse("d").matching_bits(identifier), 0U);
}

// Up to the last digit of the hash, which for "hello" is 4.
TEST(HashPrefixTest, WholeHashMatchesEveryBitOfItsIdentifier) {
    const std::string hash(hello_hash);
    const Identifier identifier("sha256", hash);
    const auto last_differs = hash.substr(0, hash.size() - 1) + "5";

    EXPECT_EQ(HashPrefix::parse(hash).matching_bits(identifier), 256U);
    EXPECT_EQ(HashPrefix::parse(last_differs).matching_bits(identifier), 255U);
}

TEST(HashPrefixTest, EmptyIdentifierSharesNoBits) {
    const Identifier empty;

    EXPECT_EQ(HashPrefix::parse("2cf2").matching_bits(empty), 0U);
}
