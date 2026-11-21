#include <gtest/gtest.h>
#include <stdexcept>
#include <string>
#include <string_view>

#include "identity/ContentDigest.hpp"

using identity::ContentDigest;

namespace {

// The body of RFC 9530 Appendix B.1's examples.
constexpr std::string_view hello_world = "{\"hello\": \"world\"}\n";
// Its sha-256, as RFC 9530 Appendix B.1 gives it.
constexpr std::string_view hello_world_sha256 =
    "sha-256=:RK/0qy18MlBSVnWgjwz6lZEWjP/lF5HF9bvEF8FabDg=:";
// Its sha-512, an algorithm not known here. Python:
// base64.b64encode(hashlib.sha512(hello_world).digest())
constexpr std::string_view hello_world_sha512 =
    "sha-512=:YMAam51Jz/jOATT6/zvHrLVgOYTGFy1d6GJiOHTohq4yP+pgk4vf2aCsyRZOtw8MjkM7iw7yZ/"
    "WkppmM44T3qg==:";

// Whether ContentDigest::parse() turns down text as invalid.
bool refused(std::string_view text) {
    try {
        (void)ContentDigest::parse(text);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

// Whether the digests text gives are those of body.
bool matches(std::string_view text, std::string_view body) {
    return ContentDigest::parse(text).matches(body);
}

} // namespace

TEST(ContentDigestTest, OfIsSha256) {
    EXPECT_EQ(ContentDigest::of(hello_world).to_string(), hello_world_sha256);
    // Python: base64.b64encode(hashlib.sha256(b"").digest())
    EXPECT_EQ(ContentDigest::of("").to_string(),
              "sha-256=:47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=:");
}

TEST(ContentDigestTest, OfMatchesItsBody) {
    EXPECT_TRUE(ContentDigest::of(hello_world).matches(hello_world));
    EXPECT_TRUE(ContentDigest::of("").matches(""));
    EXPECT_FALSE(ContentDigest::of(hello_world).matches("{\"hello\": \"world\"}"));
    EXPECT_FALSE(ContentDigest::of("").matches(hello_world));
}

TEST(ContentDigestTest, ReadsWhatItWrites) {
    const auto text = ContentDigest::of(hello_world).to_string();

    EXPECT_EQ(ContentDigest::parse(text).to_string(), text);
}

TEST(ContentDigestTest, KnownDigestIsChecked) {
    EXPECT_TRUE(matches(hello_world_sha256, hello_world));
    EXPECT_FALSE(matches(hello_world_sha256, "other"));
    EXPECT_FALSE(matches(hello_world_sha256, ""));
}

// Base64 is not lower-cased, as a content hash's hex is.
TEST(ContentDigestTest, CaseOfDigestCounts) {
    EXPECT_FALSE(matches("sha-256=:rk/0qy18mlbsvnwgjwz6lzewjp/lf5hf9bvef8fabdg=:", hello_world));
}

TEST(ContentDigestTest, DigestOfAnotherSizeDoesNotMatch) {
    EXPECT_FALSE(matches("sha-256=:RK/0qy18MlBSVnWgjwz6lZEWjP/lF5HF9bvEF8FabA==:", hello_world));
    EXPECT_FALSE(matches("sha-256=::", hello_world));
    EXPECT_FALSE(matches("sha-256=::", ""));
}

TEST(ContentDigestTest, UnknownAlgorithmsArePassedOver) {
    const std::string sha256(hello_world_sha256);

    EXPECT_TRUE(matches("md5=:AAAA:, " + sha256, hello_world));
    EXPECT_TRUE(matches(sha256 + ", unixsum=\"text\", crc32c=(1 2)", hello_world));
    EXPECT_FALSE(matches("md5=:AAAA:, " + sha256, "other"));
}

TEST(ContentDigestTest, UnknownAlgorithmsAreKept) {
    const auto text = std::string(hello_world_sha512) + ", " + std::string(hello_world_sha256);

    EXPECT_EQ(ContentDigest::parse(text).to_string(), text);
}

TEST(ContentDigestTest, ParametersArePassedOver) {
    EXPECT_TRUE(matches(std::string(hello_world_sha256) + ";note=\"text\"", hello_world));
}

TEST(ContentDigestTest, NoKnownAlgorithmIsRefused) {
    EXPECT_TRUE(refused(""));
    EXPECT_TRUE(refused("md5=:AAAA:"));
    EXPECT_TRUE(refused("unixsum=:AAAA:, md5=:AAAA:"));
    EXPECT_TRUE(refused(hello_world_sha512));
}

TEST(ContentDigestTest, KnownDigestNotOfBytesIsRefused) {
    EXPECT_TRUE(refused("sha-256=\"RK/0qy18MlBSVnWgjwz6lZEWjP/lF5HF9bvEF8FabDg=\""));
    EXPECT_TRUE(refused("sha-256=42"));
    EXPECT_TRUE(refused("sha-256=token"));
    EXPECT_TRUE(refused("sha-256"));
    EXPECT_TRUE(refused("sha-256=?0"));
    EXPECT_TRUE(refused("sha-256=(:RK/0qy18MlBSVnWgjwz6lZEWjP/lF5HF9bvEF8FabDg=:)"));
    EXPECT_TRUE(refused("md5=:AAAA:, sha-256=\"text\""));
}

TEST(ContentDigestTest, MalformedFieldIsRefused) {
    EXPECT_TRUE(refused("sha-256=:not base64"));
    EXPECT_TRUE(refused("sha-256=:not base64:"));
    EXPECT_TRUE(refused("sha-256=:\xC3\xA9:"));
    EXPECT_TRUE(refused("SHA-256=:RK/0qy18MlBSVnWgjwz6lZEWjP/lF5HF9bvEF8FabDg=:"));
    EXPECT_TRUE(refused(std::string(hello_world_sha256) + ","));
}
