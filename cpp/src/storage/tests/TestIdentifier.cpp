#include <gtest/gtest.h>
#include <stdexcept>
#include <string>
#include <utility>

#include "Fixtures.hpp"
#include "storage/Identifier.hpp"

using fixtures::bytes_of;
using storage::Identifier;

namespace {

// Whether Identifier::from() turns down text as invalid.
bool refused(const std::string& text) {
    try {
        (void)Identifier::from(text);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

} // namespace

TEST(IdentifierTest, DefaultIsEmpty) {
    const Identifier identifier;

    EXPECT_EQ(identifier.algorithm(), "");
    EXPECT_EQ(identifier.hash(), "");
    EXPECT_EQ(identifier.to_string(), "/");
}

TEST(IdentifierTest, AlgorithmAndHash) {
    const Identifier identifier("sha256", "abc123");

    EXPECT_EQ(identifier.algorithm(), "sha256");
    EXPECT_EQ(identifier.hash(), "abc123");
    EXPECT_EQ(identifier.to_string(), "sha256/abc123");
}

TEST(IdentifierTest, FromText) {
    const auto identifier = Identifier::from(std::string("sha256/abc123"));

    EXPECT_EQ(identifier.algorithm(), "sha256");
    EXPECT_EQ(identifier.hash(), "abc123");
}

// Slashes before the algorithm, and whatever follows the hash, are not part
// of the identifier.
TEST(IdentifierTest, FromTextSkipsLeadingSlashesAndWhatFollows) {
    const auto identifier = Identifier::from(std::string("//sha256/abc123/more/parts"));

    EXPECT_EQ(identifier.algorithm(), "sha256");
    EXPECT_EQ(identifier.hash(), "abc123");
}

TEST(IdentifierTest, TextWithoutAHashIsRefused) {
    EXPECT_TRUE(refused(""));
    EXPECT_TRUE(refused("///"));
    EXPECT_TRUE(refused("sha256"));
    EXPECT_TRUE(refused("/sha256"));
}

// An algorithm names a directory in a store, so ".." would reach the
// directory above it (issue #782).
TEST(IdentifierTest, UnsupportedAlgorithmIsRefused) {
    EXPECT_TRUE(refused("../abcdef"));
    EXPECT_TRUE(refused("./abcdef"));
    EXPECT_TRUE(refused("/../abcdef"));
    EXPECT_TRUE(refused("md5/abcdef"));
    EXPECT_TRUE(refused("sha2560/abcdef"));
    EXPECT_THROW((void)Identifier("..", "abcdef"), std::invalid_argument);
}

// The SHA-256 digests are those `shasum -a 256` gives for the same bytes.
TEST(IdentifierTest, FromDataIsItsSha256) {
    const auto identifier = Identifier::from(bytes_of("hello"));
    const auto empty = Identifier::from(Identifier::Data{});

    EXPECT_EQ(identifier.to_string(),
              "sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824");
    EXPECT_EQ(empty.to_string(),
              "sha256/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
}

TEST(IdentifierTest, CopyAndMove) {
    Identifier original("sha256", "abc123");

    Identifier copy(original);
    Identifier assigned;
    assigned = copy;
    Identifier moved(std::move(copy));
    Identifier move_assigned;
    move_assigned = std::move(assigned);

    EXPECT_EQ(original.to_string(), "sha256/abc123");
    EXPECT_EQ(moved.to_string(), "sha256/abc123");
    EXPECT_EQ(move_assigned.to_string(), "sha256/abc123");
}
