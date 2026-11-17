#include <gtest/gtest.h>
#include <cstddef>
#include <stdexcept>
#include <string>
#include <vector>

#include "crypto/Pem.hpp"

using crypto::Pem;

namespace {

// Enough bytes that their Base64 runs past one 64-character line.
constexpr auto bytes_past_one_line = 64;

} // namespace

TEST(PemTest, WrapBasic) {
    const std::vector<std::byte> data = {std::byte{'a'}, std::byte{'b'}, std::byte{'c'}};

    EXPECT_EQ(Pem::wrap("PUBLIC KEY", data),
              "-----BEGIN PUBLIC KEY-----\n"
              "YWJj\n"
              "-----END PUBLIC KEY-----\n");
}

TEST(PemTest, WrapLineWrapping) {
    const std::vector<std::byte> data(bytes_past_one_line, std::byte{'A'});
    const std::string pem = Pem::wrap("PRIVATE KEY", data);

    EXPECT_EQ(pem.rfind("-----BEGIN PRIVATE KEY-----\n", 0), 0U);
    EXPECT_NE(pem.find("-----END PRIVATE KEY-----\n"), std::string::npos);
    EXPECT_EQ(pem,
              "-----BEGIN PRIVATE KEY-----\n"
              "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB\n"
              "QUFBQUFBQUFBQUFBQUFBQQ==\n"
              "-----END PRIVATE KEY-----\n");
}

TEST(PemTest, UnwrapValid) {
    const std::string pem =
        "-----BEGIN PUBLIC KEY-----\n"
        "YWJj\n"
        "-----END PUBLIC KEY-----\n";
    const auto [label, data] = Pem::unwrap(pem);

    EXPECT_EQ(label, "PUBLIC KEY");
    EXPECT_EQ(data, (std::vector<std::byte>{std::byte{'a'}, std::byte{'b'}, std::byte{'c'}}));
}

TEST(PemTest, UnwrapWithWhitespace) {
    const std::string pem =
        "\n  -----BEGIN RSA PRIVATE KEY-----\r\n"
        " YWJj \n"
        "-----END RSA PRIVATE KEY-----  \n\n";
    const auto [label, data] = Pem::unwrap(pem);

    EXPECT_EQ(label, "RSA PRIVATE KEY");
    EXPECT_EQ(data.size(), 3U);
}

TEST(PemTest, UnwrapMissingHeader) {
    const std::string pem =
        "YWJj\n"
        "-----END PUBLIC KEY-----\n";

    EXPECT_THROW((void)Pem::unwrap(pem), std::invalid_argument);
}

TEST(PemTest, UnwrapMissingFooter) {
    const std::string pem =
        "-----BEGIN PUBLIC KEY-----\n"
        "YWJj\n";

    EXPECT_THROW((void)Pem::unwrap(pem), std::invalid_argument);
}

TEST(PemTest, UnwrapMismatchedLabel) {
    const std::string pem =
        "-----BEGIN PUBLIC KEY-----\n"
        "YWJj\n"
        "-----END PRIVATE KEY-----\n";

    EXPECT_THROW((void)Pem::unwrap(pem), std::invalid_argument);
}
