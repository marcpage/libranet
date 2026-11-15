#include <gtest/gtest.h>
#include <cstddef>
#include <string>

#include "crypto/IHasher.hpp"

using crypto::IHasher;

namespace {

// SHA-256's sizes, in bytes.
constexpr auto sha256_digest_size = 32U;
constexpr auto sha256_block_size = 64U;

// SHA-256 of "abc".
constexpr auto abc_digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

} // namespace

TEST(IHasherTest, Metadata) {
    const auto hasher = IHasher::Sha256();

    EXPECT_EQ(hasher->digest_size(), sha256_digest_size);
    EXPECT_EQ(hasher->block_size(), sha256_block_size);
}

TEST(IHasherTest, OneShotAndHexDigest) {
    const auto hasher = IHasher::Sha256();

    EXPECT_EQ(hasher->hex_digest(IHasher::Data{std::byte{'a'}, std::byte{'b'}, std::byte{'c'}}),
              abc_digest);
}

TEST(IHasherTest, StreamingUpdates) {
    const auto hasher = IHasher::Sha256();

    hasher->update("abcd");
    hasher->update(IHasher::Data{std::byte{'e'}, std::byte{'f'}});

    // Hash of "abcdef" (hex_digest finalizes and resets)
    EXPECT_EQ(hasher->hex_digest(),
              "bef57ec7f53a6d40beb640a780a639c83bc29ac8a9816f1fc6c5c6dcd93c4721");
}

TEST(IHasherTest, Reset) {
    const auto hasher = IHasher::Sha256();

    hasher->update("garbage data");
    (*hasher).reset();

    // After reset, hashing "abc" should yield the correct "abc" hash, not corrupted state
    hasher->update("abc");
    EXPECT_EQ(hasher->hex_digest(), abc_digest);
}

TEST(IHasherTest, DigestResets) {
    const auto hasher = IHasher::Sha256();

    hasher->update("garbage data");
    static_cast<void>(hasher->digest());
    hasher->update("abc");
    EXPECT_EQ(hasher->hex_digest(), abc_digest);
}

TEST(IHasherTest, Clone) {
    const auto hasher1 = IHasher::Sha256();

    hasher1->update("hello");

    // Clone mid-stream (cloning preserves current active context without affecting hasher1)
    const auto hasher2 = hasher1->clone();

    // Continue hasher1 with " world" -> "hello world"
    hasher1->update(" world");
    // Continue hasher2 with " there" -> "hello there"
    hasher2->update(" there");

    EXPECT_EQ(hasher1->hex_digest(),
              "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9");
    EXPECT_EQ(hasher2->hex_digest(),
              "12998c017066eb0d2a70b94e6ed3192985855ce390f321bbdb832022888bd251");
}
