#include <gtest/gtest.h>
#include <cstddef>
#include <sstream>
#include <string>

#include "http/Request.hpp"

using http::Request;

TEST(RequestTest, ConstructorsAndAccessors) {
    // Default constructor state
    Request request;
    EXPECT_TRUE(request.method().empty());
    EXPECT_TRUE(request.uri().empty());
    EXPECT_EQ(request.version(), "HTTP/1.1"); // Default version
    EXPECT_TRUE(request.body().empty());
    EXPECT_TRUE(request.headers().empty());

    // Setters and getters
    request.set_method("POST");
    request.set_uri("/api/v1/submit");
    request.set_version("HTTP/1.0");
    request.set_body("Hello, Server!");

    EXPECT_EQ(request.method(), "POST");
    EXPECT_EQ(request.uri(), "/api/v1/submit");
    EXPECT_EQ(request.version(), "HTTP/1.0");
    EXPECT_EQ(request.body(), "Hello, Server!");

    // Modifying headers through composition
    request.headers().set("Content-Type", "text/plain");
    EXPECT_EQ(request.headers().get("content-type"), "text/plain");
}

TEST(RequestTest, ParseRequestLine) {
    Request request;

    // Valid request line
    const bool success = request.parse_request_line("GET /index.html HTTP/1.1\r\n");
    EXPECT_TRUE(success);
    EXPECT_EQ(request.method(), "GET");
    EXPECT_EQ(request.uri(), "/index.html");
    EXPECT_EQ(request.version(), "HTTP/1.1");

    // Valid request line with extra spacing
    const bool success_spaced = request.parse_request_line("   POST   /submit   HTTP/1.1   ");
    EXPECT_TRUE(success_spaced);
    EXPECT_EQ(request.method(), "POST");
    EXPECT_EQ(request.uri(), "/submit");
    EXPECT_EQ(request.version(), "HTTP/1.1");

    // Malformed: missing version
    EXPECT_FALSE(request.parse_request_line("GET /index.html"));

    // Malformed: missing URI and version
    EXPECT_FALSE(request.parse_request_line("GET"));

    // Malformed: empty line
    EXPECT_FALSE(request.parse_request_line(""));
}

TEST(RequestTest, StreamParsingAndBody) {
    std::istringstream in_stream(
        "POST /api/data HTTP/1.1\r\n"
        "Host: api.example.com\r\n"
        "Content-Length: 15\r\n"
        "\r\n"
        "{\"key\":\"value\"}");

    Request request;
    in_stream >> request;

    EXPECT_FALSE(in_stream.fail());
    EXPECT_EQ(request.method(), "POST");
    EXPECT_EQ(request.uri(), "/api/data");
    EXPECT_EQ(request.version(), "HTTP/1.1");
    EXPECT_EQ(request.headers().get("host"), "api.example.com");
    EXPECT_EQ(request.headers().get("content-length"), "15");
    EXPECT_EQ(request.body(), "{\"key\":\"value\"}");
}

TEST(RequestTest, ContentLengthBeyondStream) {
    // A length far past what arrives fails the stream, without first taking
    // that much memory.
    std::istringstream in_stream(
        "POST /upload HTTP/1.1\r\n"
        "Content-Length: 99999999999\r\n"
        "\r\n"
        "partial");

    Request request;
    in_stream >> request;

    EXPECT_TRUE(in_stream.fail());
    EXPECT_EQ(request.body(), "partial");
}

TEST(RequestTest, BodyLongerThanOnePiece) {
    constexpr std::size_t body_bytes = 100000;
    const std::string body(body_bytes, 'x');
    std::istringstream in_stream(
        std::string("POST /upload HTTP/1.1\r\n")
        + "Content-Length: " + std::to_string(body_bytes) + "\r\n"
        + "\r\n"
        + body);

    Request request;
    in_stream >> request;

    EXPECT_FALSE(in_stream.fail());
    EXPECT_EQ(request.body(), body);
}

TEST(RequestTest, Serialization) {
    Request request;
    request.set_method("GET");
    request.set_uri("/test");
    request.set_version("HTTP/1.1");
    request.headers().set("Host", "localhost");

    const std::string serialized = request.to_string();

    // Request line + headers + blank line + body (empty)
    const std::string expected =
        "GET /test HTTP/1.1\r\n"
        "Host: localhost\r\n"
        "\r\n";

    EXPECT_EQ(serialized, expected);
}

TEST(RequestTest, HeadersOfAConstRequest) {
    Request request;
    request.headers().set("Host", "localhost");

    const Request& const_request = request;

    EXPECT_EQ(const_request.headers().get("host"), "localhost");
}

TEST(RequestTest, StreamWithoutARequestLineFails) {
    std::istringstream empty_stream("");
    std::istringstream bad_line_stream("GET\r\n\r\n");
    Request request;

    empty_stream >> request;
    bad_line_stream >> request;

    EXPECT_TRUE(empty_stream.fail());
    EXPECT_TRUE(bad_line_stream.fail());
}

// The headers end with an empty line, which this stream never reaches.
TEST(RequestTest, StreamEndingInTheHeadersFails) {
    std::istringstream in_stream("GET / HTTP/1.1\r\nHost: localhost\r\n");
    Request request;

    in_stream >> request;

    EXPECT_TRUE(in_stream.fail());
}

TEST(RequestTest, StreamWithContentLengthNotANumberFails) {
    std::istringstream in_stream("POST / HTTP/1.1\r\nContent-Length: five\r\n\r\nhello");
    Request request;

    in_stream >> request;

    EXPECT_TRUE(in_stream.fail());
}

TEST(RequestTest, StreamWithContentLengthOutOfRangeFails) {
    std::istringstream in_stream(
        "POST / HTTP/1.1\r\nContent-Length: 99999999999999999999999\r\n\r\nhello");
    Request request;

    in_stream >> request;

    EXPECT_TRUE(in_stream.fail());
}

// With no Content-Length, nothing after the headers is the body.
TEST(RequestTest, StreamWithoutContentLengthHasNoBody) {
    std::istringstream in_stream("GET / HTTP/1.1\r\n\r\nnot the body");
    Request request;
    request.set_body("old body");

    in_stream >> request;

    EXPECT_FALSE(in_stream.fail());
    EXPECT_EQ(request.body(), "");
}
