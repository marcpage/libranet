#include <gtest/gtest.h>
#include <cstddef>
#include <memory>
#include <string>

#include "crypto/IHasher.hpp"

using crypto::IHasher;

namespace {

// SHA-256's sizes, in bytes.
constexpr auto sha256_digest_size = 32U;
constexpr auto sha256_block_size = 64U;

// SHA-256 of "abc".
constexpr auto abc_digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

// Makes the SHA-256 a test runs on.
using MakeHasher = IHasher::Ptr (*)();

#if __APPLE__
#include "crypto/Sha256OpenSsl.hpp"

IHasher::Ptr make_open_ssl() {
    return std::make_unique<crypto::Sha256OpenSsl>();
}
#endif

// Each test runs on what IHasher::Sha256() makes: Apple's on macOS, and
// OpenSSL's elsewhere. On macOS, each runs on OpenSSL's as well.
class IHasherTest : public testing::TestWithParam<MakeHasher> {};

} // namespace

TEST_P(IHasherTest, Metadata) {
    const auto hasher = GetParam()();

    EXPECT_EQ(hasher->digest_size(), sha256_digest_size);
    EXPECT_EQ(hasher->block_size(), sha256_block_size);
}

TEST_P(IHasherTest, OneShotAndHexDigest) {
    const auto hasher = GetParam()();

    EXPECT_EQ(hasher->hex_digest(IHasher::Data{std::byte{'a'}, std::byte{'b'}, std::byte{'c'}}),
              abc_digest);
}

TEST_P(IHasherTest, EmptyInput) {
    const auto hasher = GetParam()();

    EXPECT_EQ(hasher->hex_digest(),
              "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
}

TEST_P(IHasherTest, StreamingUpdates) {
    const auto hasher = GetParam()();

    hasher->update("abcd");
    hasher->update(IHasher::Data{std::byte{'e'}, std::byte{'f'}});

    // Hash of "abcdef" (hex_digest finalizes and resets)
    EXPECT_EQ(hasher->hex_digest(),
              "bef57ec7f53a6d40beb640a780a639c83bc29ac8a9816f1fc6c5c6dcd93c4721");
}

TEST_P(IHasherTest, Reset) {
    const auto hasher = GetParam()();

    hasher->update("garbage data");
    (*hasher).reset();

    // After reset, hashing "abc" should yield the correct "abc" hash, not corrupted state
    hasher->update("abc");
    EXPECT_EQ(hasher->hex_digest(), abc_digest);
}

TEST_P(IHasherTest, DigestResets) {
    const auto hasher = GetParam()();

    hasher->update("garbage data");
    (void)hasher->digest();
    hasher->update("abc");
    EXPECT_EQ(hasher->hex_digest(), abc_digest);
}

TEST_P(IHasherTest, Clone) {
    const auto hasher1 = GetParam()();

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

INSTANTIATE_TEST_SUITE_P(Factory, IHasherTest, testing::Values(&IHasher::Sha256));
#if __APPLE__
INSTANTIATE_TEST_SUITE_P(OpenSsl, IHasherTest, testing::Values(&make_open_ssl));
#endif
