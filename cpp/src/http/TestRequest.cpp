#include <gtest/gtest.h>
#include <sstream>
#include <string>
#include <stdexcept>
#include "Request.hpp"

using namespace http;

TEST(RequestTest, ConstructorsAndAccessors) {
    // Default constructor state
    Request req;
    EXPECT_TRUE(req.method().empty());
    EXPECT_TRUE(req.uri().empty());
    EXPECT_EQ(req.version(), "HTTP/1.1"); // Default version
    EXPECT_TRUE(req.body().empty());
    EXPECT_TRUE(req.headers().empty());

    // Setters and getters
    req.set_method("POST");
    req.set_uri("/api/v1/submit");
    req.set_version("HTTP/1.0");
    req.set_body("Hello, Server!");

    EXPECT_EQ(req.method(), "POST");
    EXPECT_EQ(req.uri(), "/api/v1/submit");
    EXPECT_EQ(req.version(), "HTTP/1.0");
    EXPECT_EQ(req.body(), "Hello, Server!");

    // Modifying headers through composition
    req.headers().set("Content-Type", "text/plain");
    EXPECT_EQ(req.headers().get("content-type"), "text/plain");
}

TEST(RequestTest, ParseRequestLine) {
    Request req;

    // Valid request line
    bool success = req.parse_request_line("GET /index.html HTTP/1.1\r\n");
    EXPECT_TRUE(success);
    EXPECT_EQ(req.method(), "GET");
    EXPECT_EQ(req.uri(), "/index.html");
    EXPECT_EQ(req.version(), "HTTP/1.1");

    // Valid request line with extra spacing
    bool success_spaced = req.parse_request_line("   POST   /submit   HTTP/1.1   ");
    EXPECT_TRUE(success_spaced);
    EXPECT_EQ(req.method(), "POST");
    EXPECT_EQ(req.uri(), "/submit");
    EXPECT_EQ(req.version(), "HTTP/1.1");

    // Malformed: missing version
    EXPECT_FALSE(req.parse_request_line("GET /index.html"));

    // Malformed: missing URI and version
    EXPECT_FALSE(req.parse_request_line("GET"));

    // Malformed: empty line
    EXPECT_FALSE(req.parse_request_line(""));
}

TEST(RequestTest, StreamParsingAndBody) {
    std::istringstream iss(
        "POST /api/data HTTP/1.1\r\n"
        "Host: api.example.com\r\n"
        "Content-Length: 15\r\n"
        "\r\n"
        "{\"key\":\"value\"}");

    Request req;
    iss >> req;

    EXPECT_FALSE(iss.fail());
    EXPECT_EQ(req.method(), "POST");
    EXPECT_EQ(req.uri(), "/api/data");
    EXPECT_EQ(req.version(), "HTTP/1.1");
    EXPECT_EQ(req.headers().get("host"), "api.example.com");
    EXPECT_EQ(req.headers().get("content-length"), "15");
    EXPECT_EQ(req.body(), "{\"key\":\"value\"}");
}

TEST(RequestTest, Serialization) {
    Request req;
    req.set_method("GET");
    req.set_uri("/test");
    req.set_version("HTTP/1.1");
    req.headers().set("Host", "localhost");

    std::string serialized = req.to_string();

    // Request line + headers + blank line + body (empty)
    std::string expected =
        "GET /test HTTP/1.1\r\n"
        "Host: localhost\r\n"
        "\r\n";

    EXPECT_EQ(serialized, expected);
}
