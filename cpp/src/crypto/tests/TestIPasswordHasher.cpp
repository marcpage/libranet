#include <gtest/gtest.h>
#include <string>

#include "crypto/IPasswordHasher.hpp"

using crypto::IPasswordHasher;

namespace {

// RFC 9106's second recommended setting, which the Python node uses: 64 MiB
// in 3 passes, over 4 lanes.
constexpr auto memory_cost_kib = 65'536U;
constexpr auto iterations = 3U;
constexpr auto parallelism = 4U;
constexpr auto salt_length = 16U;
constexpr auto hash_length = 32U;

// Costs low enough for tests that hash many times.
constexpr auto quick_memory_cost_kib = 1'024U;
constexpr auto quick_iterations = 2U;

// Hashes Python's cryptography package made, with
// Argon2id(salt=b"libranet-salt-16", length=32, ...).derive_phc_encoded():
// with iterations=2, lanes=4, memory_cost=1024, of
// "correct horse battery staple"; and with iterations=3, lanes=1,
// memory_cost=65536, of "SecurePassword123!".
constexpr auto python_four_lanes =
    "$argon2id$v=19$m=1024,t=2,p=4$bGlicmFuZXQtc2FsdC0xNg"
    "$SYFVykzo0sOjFqvKEFZvJm01d6wXaLUqr9xT8/1ONqE";
constexpr auto python_one_lane =
    "$argon2id$v=19$m=65536,t=3,p=1$bGlicmFuZXQtc2FsdC0xNg"
    "$78lwJ582sqaKETY34LOhZjG0nobCJOV7u97RWaaxZEQ";

} // namespace

TEST(IPasswordHasherTest, Argon2idHashingAndVerification) {
    const auto hasher = IPasswordHasher::Argon2id(memory_cost_kib, iterations, parallelism,
                                                  salt_length, hash_length);
    const std::string password = "SecurePassword123!";
    const std::string hash = hasher->hash_password(password);

    EXPECT_EQ(hash.rfind("$argon2id$v=19$m=65536,t=3,p=4$", 0), 0U);
    EXPECT_TRUE(hasher->verify_password(password, hash));
    EXPECT_FALSE(hasher->verify_password("WrongPassword123!", hash));
}

TEST(IPasswordHasherTest, UniqueSalts) {
    const auto hasher = IPasswordHasher::Argon2id(quick_memory_cost_kib, quick_iterations, 1,
                                                  salt_length, hash_length);
    const std::string password = "SamePassword";
    const std::string hash1 = hasher->hash_password(password);
    const std::string hash2 = hasher->hash_password(password);

    // Hashes must be unique due to cryptographically secure random salting
    // Mistaken: each hash_password() call makes a salt of its own.
    // cppcheck-suppress knownConditionTrueFalse
    EXPECT_NE(hash1, hash2);
    EXPECT_TRUE(hasher->verify_password(password, hash1));
    EXPECT_TRUE(hasher->verify_password(password, hash2));
}

TEST(IPasswordHasherTest, EmptyPassword) {
    const auto hasher = IPasswordHasher::Argon2id(quick_memory_cost_kib, quick_iterations, 1,
                                                  salt_length, hash_length);
    const std::string hash = hasher->hash_password("");

    EXPECT_TRUE(hasher->verify_password("", hash));
    EXPECT_FALSE(hasher->verify_password("x", hash));
}

TEST(IPasswordHasherTest, VerifiesPythonHashes) {
    // The hash names its costs, so a hasher set up with other costs checks it.
    const auto hasher = IPasswordHasher::Argon2id(quick_memory_cost_kib, quick_iterations, 1,
                                                  salt_length, hash_length);

    EXPECT_TRUE(hasher->verify_password("correct horse battery staple", python_four_lanes));
    EXPECT_FALSE(hasher->verify_password("correct horse battery stapler", python_four_lanes));
    EXPECT_TRUE(hasher->verify_password("SecurePassword123!", python_one_lane));
}

TEST(IPasswordHasherTest, RejectsMalformedHashes) {
    const auto hasher = IPasswordHasher::Argon2id(quick_memory_cost_kib, quick_iterations, 1,
                                                  salt_length, hash_length);
    const std::string password = "correct horse battery staple";
    const std::string hash_part = "$SYFVykzo0sOjFqvKEFZvJm01d6wXaLUqr9xT8/1ONqE";

    for (const std::string& malformed : {
             std::string(),
             std::string("not a hash"),
             // Another variant, or another version.
             "$argon2i$v=19$m=1024,t=2,p=4$bGlicmFuZXQtc2FsdC0xNg" + hash_part,
             "$argon2id$v=16$m=1024,t=2,p=4$bGlicmFuZXQtc2FsdC0xNg" + hash_part,
             // Costs missing, out of order, or too large for 32 bits.
             "$argon2id$v=19$m=1024,t=2$bGlicmFuZXQtc2FsdC0xNg" + hash_part,
             "$argon2id$v=19$t=2,m=1024,p=4$bGlicmFuZXQtc2FsdC0xNg" + hash_part,
             "$argon2id$v=19$m=4294967296,t=2,p=4$bGlicmFuZXQtc2FsdC0xNg" + hash_part,
             // Less memory than 4 lanes need, which OpenSSL turns down.
             "$argon2id$v=19$m=8,t=2,p=4$bGlicmFuZXQtc2FsdC0xNg" + hash_part,
             // No hash, padding, or a character outside Base64.
             std::string("$argon2id$v=19$m=1024,t=2,p=4$bGlicmFuZXQtc2FsdC0xNg"),
             std::string("$argon2id$v=19$m=1024,t=2,p=4$bGlicmFuZXQtc2FsdC0xNg$"),
             "$argon2id$v=19$m=1024,t=2,p=4$bGlicmFuZXQtc2FsdC0xNg==" + hash_part,
             "$argon2id$v=19$m=1024,t=2,p=4$bGlicmFuZXQtc2FsdC0x!g" + hash_part,
             // A Base64 length no bytes encode to.
             "$argon2id$v=19$m=1024,t=2,p=4$bGlicmFuZXQtc2FsdC0xNgA" + hash_part,
         }) {
        EXPECT_FALSE(hasher->verify_password(password, malformed)) << malformed;
    }
}
