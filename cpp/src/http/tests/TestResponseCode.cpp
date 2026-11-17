#include <gtest/gtest.h>
#include <cstdint>

#include "http/ResponseCode.hpp"

using http::ResponseCode;
using Status = ResponseCode::Status;

namespace {

// Codes at the edges of the classes of codes, which no status names.
constexpr std::uint16_t below_informational = 99;
constexpr std::uint16_t last_informational = 199;
constexpr std::uint16_t last_success = 299;
constexpr std::uint16_t last_redirection = 399;
constexpr std::uint16_t last_client_error = 499;
constexpr std::uint16_t last_server_error = 599;
constexpr std::uint16_t above_server_error = 600;

} // namespace

// Each test checks one helper at the edges of its class: the code before
// the class's first, its first and last, and the code after its last.

TEST(ResponseCodeTest, IsInformational) {
    EXPECT_FALSE(ResponseCode(below_informational).is_informational());
    EXPECT_TRUE(ResponseCode(Status::Continue).is_informational());
    EXPECT_TRUE(ResponseCode(last_informational).is_informational());
    EXPECT_FALSE(ResponseCode(Status::Ok).is_informational());
}

TEST(ResponseCodeTest, IsSuccess) {
    EXPECT_FALSE(ResponseCode(last_informational).is_success());
    EXPECT_TRUE(ResponseCode(Status::Ok).is_success());
    EXPECT_TRUE(ResponseCode(last_success).is_success());
    EXPECT_FALSE(ResponseCode(Status::MultipleChoices).is_success());
}

TEST(ResponseCodeTest, IsRedirection) {
    EXPECT_FALSE(ResponseCode(last_success).is_redirection());
    EXPECT_TRUE(ResponseCode(Status::MultipleChoices).is_redirection());
    EXPECT_TRUE(ResponseCode(last_redirection).is_redirection());
    EXPECT_FALSE(ResponseCode(Status::BadRequest).is_redirection());
}

TEST(ResponseCodeTest, IsClientError) {
    EXPECT_FALSE(ResponseCode(last_redirection).is_client_error());
    EXPECT_TRUE(ResponseCode(Status::BadRequest).is_client_error());
    EXPECT_TRUE(ResponseCode(last_client_error).is_client_error());
    EXPECT_FALSE(ResponseCode(Status::InternalServerError).is_client_error());
}

TEST(ResponseCodeTest, IsServerError) {
    EXPECT_FALSE(ResponseCode(last_client_error).is_server_error());
    EXPECT_TRUE(ResponseCode(Status::InternalServerError).is_server_error());
    EXPECT_TRUE(ResponseCode(last_server_error).is_server_error());
    EXPECT_FALSE(ResponseCode(above_server_error).is_server_error());
}

// Client errors and server errors are both errors.
TEST(ResponseCodeTest, IsError) {
    EXPECT_FALSE(ResponseCode(last_redirection).is_error());
    EXPECT_TRUE(ResponseCode(Status::BadRequest).is_error());
    EXPECT_TRUE(ResponseCode(last_client_error).is_error());
    EXPECT_TRUE(ResponseCode(Status::InternalServerError).is_error());
    EXPECT_TRUE(ResponseCode(last_server_error).is_error());
    EXPECT_FALSE(ResponseCode(above_server_error).is_error());
}
