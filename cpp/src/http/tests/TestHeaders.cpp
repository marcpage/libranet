#include <gtest/gtest.h>
#include <cstddef>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "http/Headers.hpp"

using http::Headers;

// The tests read optionals after ASSERT_TRUE(x.has_value()), which this
// check does not follow, or with value(), whose exception gtest reports as
// a failure.
// NOLINTBEGIN(bugprone-unchecked-optional-access)

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

    auto value = headers.get("CONTENT-TYPE");
    ASSERT_TRUE(value.has_value());
    EXPECT_EQ(value.value(), "application/json");

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
    Headers headers;
    headers.set("Server", "CustomCppServer");
    headers.set("Content-Length", "1024");

    Headers recased_headers;
    // Key storage order / capitalization difference check if map handles it
    recased_headers.set("content-length", "1024");
    recased_headers.set("server", "CustomCppServer");

    EXPECT_TRUE(headers == recased_headers);

    recased_headers.set("X-Extra", "foo");
    EXPECT_TRUE(headers != recased_headers);

    // Test serialization output format
    const std::string serialized = headers.to_string();
    // Headers are stored in sorted order due to std::map
    const std::string expected = "Content-Length: 1024\r\nServer: CustomCppServer\r\n\r\n";
    EXPECT_EQ(serialized, expected);
}

TEST(HeadersTest, StreamOperatorsAndParsing) {
    // Valid header block input stream
    std::istringstream in_stream(
        "Host: example.com\r\n"
        "User-Agent: CppClient/1.0\r\n"
        "Accept: */*\r\n"
        "\r\n");

    Headers parsed_headers;
    in_stream >> parsed_headers;

    EXPECT_FALSE(in_stream.fail());
    EXPECT_EQ(parsed_headers.size(), 3U);
    EXPECT_EQ(parsed_headers.get("host"), "example.com");
    EXPECT_EQ(parsed_headers.get("user-agent"), "CppClient/1.0");
    EXPECT_EQ(parsed_headers.get("accept"), "*/*");
}

TEST(HeadersTest, ContinuationLinesAndEdgeCases) {
    // Test header folding / continuation lines (OBS-fold)
    std::istringstream folding_stream(
        "Subject: This is a long\r\n"
        " continuation line header\r\n"
        "\r\n");

    Headers folded_headers;
    folding_stream >> folded_headers;
    EXPECT_FALSE(folding_stream.fail());
    EXPECT_EQ(folded_headers.get("subject"), "This is a long continuation line header");

    // Malformed header exception: Missing colon
    std::istringstream missing_colon_stream("MalformedHeaderWithoutColon\r\n\r\n");
    Headers missing_colon;
    EXPECT_THROW(missing_colon_stream >> missing_colon, std::runtime_error);

    // Malformed header exception: Continuation line without preceding header
    std::istringstream orphan_stream(" Leading continuation line\r\n\r\n");
    Headers orphan;
    EXPECT_THROW(orphan_stream >> orphan, std::runtime_error);
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
    std::string key;
    std::string value;
    const bool success = Headers::parse_header_line("Content-Type:   text/html ", key, value);
    EXPECT_TRUE(success);
    EXPECT_EQ(key, "Content-Type");
    EXPECT_EQ(value, "text/html");

    const bool fail_success = Headers::parse_header_line("InvalidLine", key, value);
    EXPECT_FALSE(fail_success);
}

TEST(HeadersTest, ContinuationAddsNoSpaces) {
    // A continuation line of only spaces adds nothing, and one onto an empty
    // value adds its text alone, so the value reads back as written.
    std::istringstream stream(
        "Blank: value\r\n"
        "   \r\n"
        "Empty:\r\n"
        " more\r\n"
        "\r\n");
    Headers headers;
    stream >> headers;

    EXPECT_FALSE(stream.fail());
    EXPECT_EQ(headers.get("blank"), "value");
    EXPECT_EQ(headers.get("empty"), "more");

    std::istringstream written(headers.to_string());
    Headers reread;
    written >> reread;
    EXPECT_EQ(reread, headers);
}

// NOLINTEND(bugprone-unchecked-optional-access)

TEST(HeadersTest, MissingHeaderIsNothing) {
    const Headers headers;

    EXPECT_EQ(headers.get("Host"), std::nullopt);
}

TEST(HeadersTest, MovedHeadersKeepTheirValues) {
    Headers headers;
    headers.set("Host", "localhost");

    const Headers moved(std::move(headers));

    EXPECT_EQ(moved.get("host"), "localhost");
}

// Headers of the same size differ by a key or by a value.
TEST(HeadersTest, UnequalKeysOrValues) {
    Headers headers;
    headers.set("Host", "localhost");
    Headers other_key;
    other_key.set("Server", "localhost");
    Headers other_value;
    other_value.set("Host", "example.com");

    EXPECT_NE(headers, other_key);
    EXPECT_NE(other_key, headers);
    EXPECT_NE(headers, other_value);
}

TEST(HeadersTest, EmptyLineContinuesNothing) {
    EXPECT_FALSE(Headers::is_continuation_line(""));
}

TEST(HeadersTest, WrittenToAStream) {
    Headers headers;
    headers.set("Host", "localhost");
    std::ostringstream out_stream;

    out_stream << headers;

    EXPECT_EQ(out_stream.str(), "Host: localhost\r\n\r\n");
}

TEST(HeadersTest, ProcessHeaderLine) {
    Headers headers;

    EXPECT_EQ(Headers::process_header_line("", "Host", headers), "");
    EXPECT_EQ(Headers::process_header_line("Host: localhost", "", headers), "Host");
    EXPECT_EQ(Headers::process_header_line(" more", "Host", headers), "Host");
    EXPECT_EQ(headers.get("host"), "localhost more");
}

// A continuation is refused when the header it continues is not there.
TEST(HeadersTest, ContinuationOfAMissingHeaderIsRefused) {
    Headers headers;

    EXPECT_THROW((void)Headers::process_header_line(" more", "Host", headers),
                 std::invalid_argument);
}

// The last field holds the rest of the text, spaces and all.
TEST(HeadersTest, SplitSpaces) {
    constexpr std::size_t three_fields = 3;
    using Fields = std::vector<std::string_view>;

    EXPECT_EQ(Headers::split_spaces("GET / HTTP/1.1 extra", three_fields),
              (Fields{"GET", "/", "HTTP/1.1 extra"}));
    EXPECT_EQ(Headers::split_spaces("GET   ", three_fields), (Fields{"GET"}));
    EXPECT_EQ(Headers::split_spaces("GET /   ", three_fields), (Fields{"GET", "/"}));
    EXPECT_EQ(Headers::split_spaces("", three_fields), Fields{});
    EXPECT_EQ(Headers::split_spaces("GET / HTTP/1.1", 0), Fields{});
}
