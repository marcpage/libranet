#include <gtest/gtest.h>
#include <sstream>
#include <stdexcept>
#include <string>

#include "http/Response.hpp"

using namespace http;

TEST(ResponseTest, ConstructorsAndAccessors) {
    constexpr auto not_found_code = 404;

    // Default constructor state (HTTP/1.1 200 OK)
    Response res;
    EXPECT_EQ(res.version(), "HTTP/1.1");
    EXPECT_EQ(res.status_code(), 200);
    EXPECT_EQ(res.reason_phrase(), "OK");
    EXPECT_TRUE(res.body().empty());
    EXPECT_TRUE(res.headers().empty());

    // Setters and getters
    res.set_version("HTTP/1.0");
    res.set_status_code(not_found_code);
    res.set_reason_phrase("Not Found");
    res.set_body("Resource missing");

    EXPECT_EQ(res.version(), "HTTP/1.0");
    EXPECT_EQ(res.status_code(), not_found_code);
    EXPECT_EQ(res.reason_phrase(), "Not Found");
    EXPECT_EQ(res.body(), "Resource missing");

    // Modifying headers through composition
    res.headers().set("Content-Type", "text/plain");
    EXPECT_EQ(res.headers().get("content-type"), "text/plain");
}

TEST(ResponseTest, ParseStatusLine) {
    Response res;

    // Valid status line with reason phrase
    const bool success = res.parse_status_line("HTTP/1.1 200 OK\r\n");
    EXPECT_TRUE(success);
    EXPECT_EQ(res.version(), "HTTP/1.1");
    EXPECT_EQ(res.status_code(), 200);
    EXPECT_EQ(res.reason_phrase(), "OK");

    // Valid status line without reason phrase
    const bool success_no_reason = res.parse_status_line("HTTP/1.1 500");
    EXPECT_TRUE(success_no_reason);
    EXPECT_EQ(res.version(), "HTTP/1.1");
    EXPECT_EQ(res.status_code(), 500);
    EXPECT_TRUE(res.reason_phrase().empty());

    // Valid status line with extra spacing
    const bool success_spaced = res.parse_status_line("   HTTP/1.1   403   Forbidden   ");
    EXPECT_TRUE(success_spaced);
    EXPECT_EQ(res.version(), "HTTP/1.1");
    EXPECT_EQ(res.status_code(), 403);
    EXPECT_EQ(res.reason_phrase(), "Forbidden");

    // Malformed: non-numeric status code
    EXPECT_FALSE(res.parse_status_line("HTTP/1.1 ABC OK"));

    // Malformed: missing status code
    EXPECT_FALSE(res.parse_status_line("HTTP/1.1"));

    // Malformed: empty line
    EXPECT_FALSE(res.parse_status_line(""));
}

TEST(ResponseTest, StreamParsingAndBody) {
    std::istringstream iss(
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/plain\r\n"
        "Content-Length: 12\r\n"
        "\r\n"
        "Hello World!");

    Response res;
    iss >> res;

    EXPECT_FALSE(iss.fail());
    EXPECT_EQ(res.version(), "HTTP/1.1");
    EXPECT_EQ(res.status_code(), 200);
    EXPECT_EQ(res.reason_phrase(), "OK");
    EXPECT_EQ(res.headers().get("content-type"), "text/plain");
    EXPECT_EQ(res.headers().get("content-length"), "12");
    EXPECT_EQ(res.body(), "Hello World!");
}

TEST(ResponseTest, Serialization) {
    constexpr auto created_code = 201;

    Response res;
    res.set_version("HTTP/1.1");
    res.set_status_code(created_code);
    res.set_reason_phrase("Created");
    res.headers().set("Connection", "close");

    const std::string serialized = res.to_string();

    // Status line + headers + blank line + body (empty)
    const std::string expected =
        "HTTP/1.1 201 Created\r\n"
        "Connection: close\r\n"
        "\r\n";

    EXPECT_EQ(serialized, expected);
}
