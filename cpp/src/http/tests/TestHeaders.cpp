#include <gtest/gtest.h>
#include <sstream>
#include <stdexcept>
#include <string>

#include "http/Headers.hpp"

using namespace http;

TEST(HeadersTest, ConstructorsAndBasicAccessors) {
    // Default constructor (empty state)
    Headers headers;
    EXPECT_TRUE(headers.empty());
    EXPECT_EQ(headers.size(), 0U);
    EXPECT_FALSE(headers.has("Content-Type"));

    // Set and case-insensitive retrieval
    headers.set("Content-Type", "application/json");
    EXPECT_FALSE(headers.empty());
    EXPECT_EQ(headers.size(), 1U);
    EXPECT_TRUE(headers.has("content-type")); // Case-insensitive check
    EXPECT_TRUE(headers.has("CONTENT-TYPE"));

    auto val = headers.get("CONTENT-TYPE");
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(val.value(), "application/json");

    // Overloaded get with default value
    EXPECT_EQ(headers.get("X-Missing", "default-val"), "default-val");
    EXPECT_EQ(headers.get("Content-Type", "default-val"), "application/json");

    // Removal and clearing
    headers.remove("content-type");
    EXPECT_TRUE(headers.empty());

    headers.set("Host", "localhost");
    headers.set("Connection", "keep-alive");
    EXPECT_EQ(headers.size(), 2U);
    headers.clear();
    EXPECT_TRUE(headers.empty());
}

TEST(HeadersTest, EqualityAndSerialization) {
    Headers h1;
    h1.set("Server", "CustomCppServer");
    h1.set("Content-Length", "1024");

    Headers h2;
    h2.set("content-length", "1024"); // Key storage order / capitalization difference check if map handles it
    h2.set("server", "CustomCppServer");

    EXPECT_TRUE(h1 == h2);

    h2.set("X-Extra", "foo");
    EXPECT_TRUE(h1 != h2);

    // Test serialization output format
    std::string serialized = h1.to_string();
    // Headers are stored in sorted order due to std::map
    std::string expected = "Content-Length: 1024\r\nServer: CustomCppServer\r\n\r\n";
    EXPECT_EQ(serialized, expected);
}

TEST(HeadersTest, StreamOperatorsAndParsing) {
    // Valid header block input stream
    std::istringstream iss(
        "Host: example.com\r\n"
        "User-Agent: CppClient/1.0\r\n"
        "Accept: */*\r\n"
        "\r\n");

    Headers parsed_headers;
    iss >> parsed_headers;

    EXPECT_FALSE(iss.fail());
    EXPECT_EQ(parsed_headers.size(), 3U);
    EXPECT_EQ(parsed_headers.get("host"), "example.com");
    EXPECT_EQ(parsed_headers.get("user-agent"), "CppClient/1.0");
    EXPECT_EQ(parsed_headers.get("accept"), "*/*");
}

TEST(HeadersTest, ContinuationLinesAndEdgeCases) {
    // Test header folding / continuation lines (OBS-fold)
    std::istringstream folding_iss(
        "Subject: This is a long\r\n"
        " continuation line header\r\n"
        "\r\n");

    Headers folded_headers;
    folding_iss >> folded_headers;
    EXPECT_FALSE(folding_iss.fail());
    EXPECT_EQ(folded_headers.get("subject"), "This is a long continuation line header");

    // Malformed header exception: Missing colon
    std::istringstream missing_colon_iss("MalformedHeaderWithoutColon\r\n\r\n");
    Headers missing_colon;
    EXPECT_THROW(missing_colon_iss >> missing_colon, std::runtime_error);

    // Malformed header exception: Continuation line without preceding header
    std::istringstream orphan_iss(" Leading continuation line\r\n\r\n");
    Headers orphan;
    EXPECT_THROW(orphan_iss >> orphan, std::runtime_error);
}

TEST(HeadersTest, HelperUtilities) {
    // Trim trailing carriage return
    EXPECT_EQ(Headers::trim_trailing_cr("hello\r"), "hello");
    EXPECT_EQ(Headers::trim_trailing_cr("hello"), "hello");
    EXPECT_EQ(Headers::trim_trailing_cr(""), "");

    // Trim whitespace
    EXPECT_EQ(Headers::trim_whitespace("   trimmed   "), "trimmed");
    EXPECT_EQ(Headers::trim_whitespace("\t\tval"), "val");

    // Continuation checker
    EXPECT_TRUE(Headers::is_continuation_line(" leading space"));
    EXPECT_TRUE(Headers::is_continuation_line("\tleading tab"));
    EXPECT_FALSE(Headers::is_continuation_line("Normal-Header: value"));

    // Parse single header line
    std::string key, value;
    bool success = Headers::parse_header_line("Content-Type:   text/html ", key, value);
    EXPECT_TRUE(success);
    EXPECT_EQ(key, "Content-Type");
    EXPECT_EQ(value, "text/html");

    bool fail_success = Headers::parse_header_line("InvalidLine", key, value);
    EXPECT_FALSE(fail_success);
}
