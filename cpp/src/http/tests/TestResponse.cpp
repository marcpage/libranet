#include <gtest/gtest.h>
#include <cstddef>
#include <sstream>
#include <string>

#include "http/Response.hpp"

using http::Response;

TEST(ResponseTest, ConstructorsAndAccessors) {
    constexpr auto not_found_code = 404;

    // Default constructor state (HTTP/1.1 200 OK)
    Response response;
    EXPECT_EQ(response.version(), "HTTP/1.1");
    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.reason_phrase(), "OK");
    EXPECT_TRUE(response.body().empty());
    EXPECT_TRUE(response.headers().empty());

    // Setters and getters
    response.set_version("HTTP/1.0");
    response.set_status_code(not_found_code);
    response.set_reason_phrase("Not Found");
    response.set_body("Resource missing");

    EXPECT_EQ(response.version(), "HTTP/1.0");
    EXPECT_EQ(response.status_code(), not_found_code);
    EXPECT_EQ(response.reason_phrase(), "Not Found");
    EXPECT_EQ(response.body(), "Resource missing");

    // Modifying headers through composition
    response.headers().set("Content-Type", "text/plain");
    EXPECT_EQ(response.headers().get("content-type"), "text/plain");
}

TEST(ResponseTest, ParseStatusLine) {
    Response response;

    // Valid status line with reason phrase
    const bool success = response.parse_status_line("HTTP/1.1 200 OK\r\n");
    EXPECT_TRUE(success);
    EXPECT_EQ(response.version(), "HTTP/1.1");
    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.reason_phrase(), "OK");

    // Valid status line without reason phrase
    const bool success_no_reason = response.parse_status_line("HTTP/1.1 500");
    EXPECT_TRUE(success_no_reason);
    EXPECT_EQ(response.version(), "HTTP/1.1");
    EXPECT_EQ(response.status_code(), 500);
    EXPECT_TRUE(response.reason_phrase().empty());

    // Valid status line with extra spacing
    const bool success_spaced = response.parse_status_line("   HTTP/1.1   403   Forbidden   ");
    EXPECT_TRUE(success_spaced);
    EXPECT_EQ(response.version(), "HTTP/1.1");
    EXPECT_EQ(response.status_code(), 403);
    EXPECT_EQ(response.reason_phrase(), "Forbidden");

    // Malformed: non-numeric status code
    EXPECT_FALSE(response.parse_status_line("HTTP/1.1 ABC OK"));

    // Malformed: missing status code
    EXPECT_FALSE(response.parse_status_line("HTTP/1.1"));

    // Malformed: empty line
    EXPECT_FALSE(response.parse_status_line(""));
}

TEST(ResponseTest, StreamParsingAndBody) {
    std::istringstream in_stream(
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/plain\r\n"
        "Content-Length: 12\r\n"
        "\r\n"
        "Hello World!");

    Response response;
    in_stream >> response;

    EXPECT_FALSE(in_stream.fail());
    EXPECT_EQ(response.version(), "HTTP/1.1");
    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.reason_phrase(), "OK");
    EXPECT_EQ(response.headers().get("content-type"), "text/plain");
    EXPECT_EQ(response.headers().get("content-length"), "12");
    EXPECT_EQ(response.body(), "Hello World!");
}

TEST(ResponseTest, ContentLengthBeyondStream) {
    // A length far past what arrives fails the stream, without first taking
    // that much memory.
    std::istringstream in_stream(
        "HTTP/1.1 200 OK\r\n"
        "Content-Length: 99999999999\r\n"
        "\r\n"
        "partial");

    Response response;
    in_stream >> response;

    EXPECT_TRUE(in_stream.fail());
    EXPECT_EQ(response.body(), "partial");
}

TEST(ResponseTest, BodyLongerThanOnePiece) {
    constexpr std::size_t body_bytes = 100000;
    const std::string body(body_bytes, 'x');
    std::istringstream in_stream(
        std::string("HTTP/1.1 200 OK\r\n")
        + "Content-Length: " + std::to_string(body_bytes) + "\r\n"
        + "\r\n"
        + body);

    Response response;
    in_stream >> response;

    EXPECT_FALSE(in_stream.fail());
    EXPECT_EQ(response.body(), body);
}

TEST(ResponseTest, Serialization) {
    constexpr auto created_code = 201;

    Response response;
    response.set_version("HTTP/1.1");
    response.set_status_code(created_code);
    response.set_reason_phrase("Created");
    response.headers().set("Connection", "close");

    const std::string serialized = response.to_string();

    // Status line + headers + blank line + body (empty)
    const std::string expected =
        "HTTP/1.1 201 Created\r\n"
        "Connection: close\r\n"
        "\r\n";

    EXPECT_EQ(serialized, expected);
}
