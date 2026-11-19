#include <gtest/gtest.h>
#include <stdexcept>
#include <string>

#include "storage/Identifier.hpp"

using std::invalid_argument;
using std::string;
using storage::Identifier;

namespace {

// Whether Identifier::from() turns down text as invalid.
bool refused(const string& text) {
    try {
        (void)Identifier::from(text);
    } catch (const invalid_argument&) {
        return true;
    }

    return false;
}

} // namespace

TEST(IdentifierTest, AlgorithmAndHash) {
    const auto identifier = Identifier::from("sha256/0123abcd");

    EXPECT_EQ(identifier.algorithm(), "sha256");
    EXPECT_EQ(identifier.hash(), "0123abcd");
    EXPECT_EQ(identifier.to_string(), "sha256/0123abcd");
}

TEST(IdentifierTest, LeadingSlashesAndTrailingPathIgnored) {
    const auto identifier = Identifier::from("//sha256/0123abcd/aes/key");

    EXPECT_EQ(identifier.algorithm(), "sha256");
    EXPECT_EQ(identifier.hash(), "0123abcd");
}

TEST(IdentifierTest, FromDataIsSha256) {
    const auto identifier = Identifier::from(Identifier::Data{});

    EXPECT_EQ(identifier.to_string(),
              "sha256/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
}

TEST(IdentifierTest, MissingHashIsRefused) {
    EXPECT_TRUE(refused(""));
    EXPECT_TRUE(refused("///"));
    EXPECT_TRUE(refused("sha256"));
}

// An algorithm names a directory in a store, so ".." would reach the
// directory above it (issue #782).
TEST(IdentifierTest, UnsupportedAlgorithmIsRefused) {
    EXPECT_TRUE(refused("../abcdef"));
    EXPECT_TRUE(refused("./abcdef"));
    EXPECT_TRUE(refused("/../abcdef"));
    EXPECT_TRUE(refused("md5/abcdef"));
    EXPECT_TRUE(refused("sha2560/abcdef"));
    EXPECT_THROW((void)Identifier("..", "abcdef"), invalid_argument);
}
