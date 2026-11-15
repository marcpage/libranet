#include <gtest/gtest.h>
#include <algorithm>
#include <cstddef>
#include <stdexcept>
#include <string>
#include <vector>

#include "crypto/Base64.hpp"

using crypto::Base64;

TEST(Base64Test, EncodeString) {
    EXPECT_EQ(Base64::encode("Hello, World!"), "SGVsbG8sIFdvcmxkIQ==");
}

TEST(Base64Test, EncodeBytes) {
    const std::vector<std::byte> bytes = {std::byte{'a'}, std::byte{'b'}, std::byte{'c'}};

    EXPECT_EQ(Base64::encode(bytes), "YWJj");
}

TEST(Base64Test, DecodeValid) {
    const std::vector<std::byte> decoded = Base64::decode("SGVsbG8sIFdvcmxkIQ==");
    std::string decoded_string(decoded.size(), '\0');

    std::transform(decoded.begin(), decoded.end(), decoded_string.begin(),
                   [](std::byte byte_item) { return static_cast<char>(byte_item); });

    EXPECT_EQ(decoded_string, "Hello, World!");
}

TEST(Base64Test, DecodeInvalidLength) {
    // Length not a multiple of 4
    EXPECT_THROW(static_cast<void>(Base64::decode("abc")), std::invalid_argument);
}

TEST(Base64Test, DecodeInvalidCharacter) {
    // Contains '$' which is not part of the Base64 alphabet
    EXPECT_THROW(static_cast<void>(Base64::decode("AB$D")), std::invalid_argument);
}
