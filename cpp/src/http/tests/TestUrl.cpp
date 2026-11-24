#include <gtest/gtest.h>
#include <sstream>
#include <string>

#include "http/Url.hpp"

using http::Url;

// The tests read optionals after ASSERT_TRUE(x.has_value()), which this
// check does not follow, or with value(), whose exception gtest reports as
// a failure.
// NOLINTBEGIN(bugprone-unchecked-optional-access)

TEST(UrlTest, ConstructorsAndBasicAccessors) {
    constexpr auto port = 8080;

    // Default constructor (empty state)
    Url url;
    EXPECT_TRUE(url.scheme().empty());
    EXPECT_TRUE(url.host().empty());
    EXPECT_FALSE(url.port().has_value());
    EXPECT_EQ(url.path(), "/");
    EXPECT_TRUE(url.username().empty());
    EXPECT_TRUE(url.password().empty());
    EXPECT_TRUE(url.fragment().empty());
    EXPECT_TRUE(url.query().empty());

    // Basic setters and accessors
    url.set_scheme("https");
    url.set_host("example.com");
    url.set_port(port);
    url.set_path("/api/v1/resource");
    url.set_username("admin");
    url.set_password("secret");
    url.set_fragment("section2");

    EXPECT_EQ(url.scheme(), "https");
    EXPECT_EQ(url.host(), "example.com");
    EXPECT_EQ(url.port(), port);
    EXPECT_TRUE(url.port().has_value());
    EXPECT_EQ(url.port(), port);
    EXPECT_EQ(url.path(), "/api/v1/resource");
    EXPECT_EQ(url.username(), "admin");
    EXPECT_EQ(url.password(), "secret");
    EXPECT_EQ(url.fragment(), "section2");
}

TEST(UrlTest, UrlParsing) {
    // Comprehensive URL parsing test
    auto parsed = Url::parse(
        "https://john:doe@sub.example.com:9000/path/to/page?foo=bar&baz=123#anchor");
    ASSERT_TRUE(parsed.has_value());

    const auto& url = parsed.value();
    EXPECT_EQ(url.scheme(), "https");
    EXPECT_EQ(url.username(), "john");
    EXPECT_EQ(url.password(), "doe");
    EXPECT_EQ(url.host(), "sub.example.com");
    EXPECT_EQ(url.port().value(), 9000);
    EXPECT_TRUE(url.port().has_value());
    EXPECT_EQ(url.path(), "/path/to/page");
    EXPECT_EQ(url.fragment(), "anchor");

    const auto foo_value = url.query("foo");
    EXPECT_TRUE(foo_value.has_value());
    EXPECT_EQ(foo_value, "bar");

    const auto baz_value = url.query("baz");
    EXPECT_TRUE(baz_value.has_value());
    EXPECT_EQ(baz_value, "123");

    // Minimal URL parsing test (defaults and implicit paths)
    auto minimal = Url::parse("http://example.com");
    ASSERT_TRUE(minimal.has_value());
    EXPECT_EQ(minimal->scheme(), "http");
    EXPECT_EQ(minimal->host(), "example.com");
    EXPECT_EQ(minimal->path(), "/");
    EXPECT_FALSE(minimal->port().has_value());

    // Invalid or empty URL checks
    EXPECT_FALSE(Url::parse("").has_value());
}

TEST(UrlTest, PortValidationAndEffectivePorts) {
    // 1. Valid explicit port boundaries
    auto max_port_url = Url::parse("http://localhost:65535");
    ASSERT_TRUE(max_port_url.has_value());
    EXPECT_TRUE(max_port_url->port().has_value());
    EXPECT_EQ(max_port_url->port(), 65'535);
    EXPECT_EQ(max_port_url->effective_port(), 65'535);

    // 2. Out of range port (> 65535)
    auto overflow_port_url = Url::parse("http://localhost:65536");
    ASSERT_TRUE(overflow_port_url.has_value());
    EXPECT_FALSE(overflow_port_url->port().has_value());

    // 3. Non-numeric port string
    auto invalid_port_url = Url::parse("http://localhost:abc");
    ASSERT_TRUE(invalid_port_url.has_value());
    EXPECT_FALSE(invalid_port_url->port().has_value());

    // 4. Programmatic port number setter
    constexpr auto https_port = 443;
    Url url;
    url.set_port(https_port);
    EXPECT_TRUE(url.port().has_value());
    EXPECT_EQ(url.port(), https_port);

    // 5. Effective port fallback resolution based on scheme
    Url http_url;
    http_url.set_scheme("http");
    EXPECT_FALSE(http_url.port().has_value());
    EXPECT_TRUE(http_url.effective_port().has_value());
    EXPECT_EQ(http_url.effective_port(), 80);

    Url https_url;
    https_url.set_scheme("HTTPS");
    EXPECT_TRUE(https_url.effective_port().has_value());
    EXPECT_EQ(https_url.effective_port(), 443);

    Url ftp_url;
    ftp_url.set_scheme("ftp");
    EXPECT_TRUE(ftp_url.effective_port().has_value());
    EXPECT_EQ(ftp_url.effective_port(), 21);

    Url ws_url;
    ws_url.set_scheme("ws");
    EXPECT_TRUE(ws_url.effective_port().has_value());
    EXPECT_EQ(ws_url.effective_port(), 80);

    Url wss_url;
    wss_url.set_scheme("wss");
    EXPECT_TRUE(wss_url.effective_port().has_value());
    EXPECT_EQ(wss_url.effective_port(), 443);

    Url unknown_url;
    unknown_url.set_scheme("custom");
    EXPECT_FALSE(unknown_url.effective_port().has_value());
}

TEST(UrlTest, UrlEncodingAndDecoding) {
    const std::string raw = "hello world/test?foo=bar+baz";

    // 1. UriComponent Context
    const std::string encoded_uri = Url::encode(raw, Url::EncodeContext::UriComponent);
    EXPECT_EQ(encoded_uri, "hello%20world%2Ftest%3Ffoo%3Dbar%2Bbaz");
    EXPECT_EQ(Url::decode(encoded_uri, Url::EncodeContext::UriComponent), raw);

    // 2. FormUrl Context
    const std::string encoded_form = Url::encode(raw, Url::EncodeContext::FormUrl);
    EXPECT_EQ(encoded_form, "hello+world%2Ftest%3Ffoo%3Dbar%2Bbaz");
    EXPECT_EQ(Url::decode(encoded_form, Url::EncodeContext::FormUrl), raw);

    // 3. Custom allowed characters overload test
    const std::string custom_encoded = Url::encode("path/to/resource", "/", false);
    EXPECT_EQ(custom_encoded, "path/to/resource");
}

TEST(UrlTest, DecodeOnlyAcceptsTwoHexDigits) {
    // Valid escapes decode, in either case
    EXPECT_EQ(Url::decode("%2F%2f%41", false), "//A");
    EXPECT_EQ(Url::decode("a%41", false), "aA");

    // A '%' not followed by two hex digits is kept as-is
    EXPECT_EQ(Url::decode("%Ax", false), "%Ax");
    EXPECT_EQ(Url::decode("%A/b", false), "%A/b");
    EXPECT_EQ(Url::decode("%4G", false), "%4G");
    EXPECT_EQ(Url::decode("%G4", false), "%G4");
    EXPECT_EQ(Url::decode("% A", false), "% A");
    EXPECT_EQ(Url::decode("%-1", false), "%-1");
    EXPECT_EQ(Url::decode("%+1", false), "%+1");
    EXPECT_EQ(Url::decode("%A", false), "%A");
    EXPECT_EQ(Url::decode("%", false), "%");

    // The digits after a rejected '%' are not consumed
    EXPECT_EQ(Url::decode("%%41", false), "%A");
}

TEST(UrlTest, EncodeNonAsciiBytes) {
    // "é" in UTF-8 is the bytes C3 A9, each encoded as two hex digits
    const std::string e_acute = "\xC3\xA9";

    EXPECT_EQ(Url::encode(e_acute, Url::EncodeContext::UriComponent), "%C3%A9");
    EXPECT_EQ(Url::encode(e_acute, Url::EncodeContext::FormUrl), "%C3%A9");
    EXPECT_EQ(Url::encode("caf\xC3\xA9 au lait"), "caf%C3%A9+au+lait");
    EXPECT_EQ(Url::encode("\x80\xFF\x01", "", false), "%80%FF%01");

    // Encoded bytes decode back to the original
    EXPECT_EQ(Url::decode(Url::encode(e_acute)), e_acute);

    // A path is encoded the same way
    Url url;
    url.set_path("/caf\xC3\xA9");
    EXPECT_EQ(url.to_string(), "/caf%C3%A9");
    EXPECT_EQ(url.path(), "/caf\xC3\xA9");
}

TEST(UrlTest, TransparentComponentEncoding) {
    Url url;
    url.set_path("/my folder/sub folder/file.txt");
    url.set_username("john doe");
    url.set_password("p@ss word");
    url.set_fragment("section 3?query=true");
    url.set_query_parameters({
        {"search", "modern c++"},
        {"tag", "networking"},
    });

    // 1. Verify getters return decoded, human-readable values transparently
    EXPECT_EQ(url.path(), "/my folder/sub folder/file.txt");
    EXPECT_EQ(url.username(), "john doe");
    EXPECT_EQ(url.password(), "p@ss word");
    EXPECT_EQ(url.fragment(), "section 3?query=true");

    // 2. Verify optional accesses for query parameters
    auto search_value = url.query("search");
    ASSERT_TRUE(search_value.has_value());
    EXPECT_EQ(search_value.value(), "modern c++");

    const auto missing_value = url.query("nonexistent");
    EXPECT_FALSE(missing_value.has_value());
    EXPECT_EQ(missing_value.value_or("default"), "default");

    // 3. Verify case-insensitive comparison operators
    const Url url1 = Url::parse("HTTPS://Example.COM:443/path").value();
    const Url url2 = Url::parse("https://example.com:443/path").value();
    EXPECT_TRUE(url1 == url2);
    EXPECT_FALSE(url1 < url2);
}

TEST(UrlTest, HasAndRemoveQuery) {
    Url url;
    url.set_query_parameters({
        {"search", "modern c++"},
        {"my tag", "networking"},
    });

    // Keys are given as they read, and encoded to match the query's.
    EXPECT_TRUE(url.has_query("search"));
    EXPECT_TRUE(url.has_query("my tag"));
    EXPECT_FALSE(url.has_query("nonexistent"));

    url.remove_query("my tag");
    EXPECT_FALSE(url.has_query("my tag"));
    EXPECT_TRUE(url.has_query("search"));

    // Removing a key that is not there changes nothing.
    url.remove_query("nonexistent");
    EXPECT_TRUE(url.has_query("search"));
    EXPECT_EQ(url.query("search").value_or(""), "modern c++");
}

TEST(UrlTest, UrlAppend) {
    auto base = Url::parse("https://example.com/api/v1/posts");
    ASSERT_TRUE(base.has_value());

    auto& url = base.value();

    // 1. Append relative path segment
    url.append("123/comments");
    EXPECT_EQ(url.to_string(), "https://example.com/api/v1/123/comments");

    // 2. Append absolute path starting with '/'
    url.append("/health");
    EXPECT_EQ(url.to_string(), "https://example.com/health");

    // 3. Append query parameters and fragments
    url.append("?verbose=true#status");
    EXPECT_EQ(url.to_string(), "https://example.com/health?verbose=true#status");

    // 4. Append an absolute URL
    url.append("http://other.org/resource");
    EXPECT_EQ(url.to_string(), "http://other.org/resource");
}

TEST(UrlTest, SerializationAndReconstruction) {
    const std::string original_text = "https://user:pass@example.com:8443/search?q=cpp#top";
    auto parsed = Url::parse(original_text);
    ASSERT_TRUE(parsed.has_value());

    const std::string serialized = parsed.value().to_string();
    EXPECT_EQ(serialized, original_text);
}

TEST(UrlTest, StreamOperatorsAndParsing) {
    auto url = Url::parse("ftp://files.example.com/pub/file.txt");
    ASSERT_TRUE(url.has_value());

    std::ostringstream out_stream;
    out_stream << url.value();
    EXPECT_EQ(out_stream.str(), "ftp://files.example.com/pub/file.txt");

    std::istringstream in_stream("http://test.org/index.html?ref=home");
    Url stream_url;
    in_stream >> stream_url;

    EXPECT_FALSE(in_stream.fail());
    EXPECT_EQ(stream_url.scheme(), "http");
    EXPECT_EQ(stream_url.host(), "test.org");
    EXPECT_EQ(stream_url.path(), "/index.html");

    const auto ref_value = stream_url.query("ref");
    EXPECT_TRUE(ref_value.has_value());
    EXPECT_EQ(ref_value, "home");

    std::istringstream bad_stream("");
    Url bad_url;
    bad_stream >> bad_url;
    EXPECT_TRUE(bad_stream.fail());
}

TEST(UrlTest, WrittenAsRead) {
    // A password with no username keeps both.
    EXPECT_EQ(Url::parse("http://:secret@example.com/").value().to_string(),
              "http://:secret@example.com/");

    // A query that itself starts with '?' keeps it.
    const Url doubled = Url::parse("http://example.com/path??x=1").value();
    EXPECT_EQ(doubled.query().to_string(), "?x=1");
    EXPECT_EQ(doubled.to_string(), "http://example.com/path??x=1");

    // A "://" in the path names no scheme.
    const Url in_path = Url::parse("example.com/a://b").value();
    EXPECT_TRUE(in_path.scheme().empty());
    EXPECT_EQ(in_path.host(), "example.com");
    EXPECT_EQ(in_path.path(), "/a://b");

    // Brackets belong only around an IPv6 address.
    EXPECT_FALSE(Url::parse("http://[[]]/").has_value());
    EXPECT_FALSE(Url::parse("http://exa]mple.com/").has_value());
    EXPECT_EQ(Url::parse("http://[::1]:8080/").value().host(), "::1");
}

namespace {

// Whether first is not second, and comes before it.
bool comes_before(const Url& first, const Url& second) {
    return first != second && first < second && !(second < first);
}

} // namespace

// Urls are the same when each part is, schemes and hosts without regard to
// case.
TEST(UrlTest, SchemeAndHostComparedWithoutCase) {
    const Url url = Url::parse("http://user:pass@example.com:8080/path?a=1#top").value();
    const Url other_case = Url::parse("HTTP://user:pass@EXAMPLE.com:8080/path?a=1#top").value();

    EXPECT_EQ(url, other_case);
    EXPECT_FALSE(url < other_case);
    EXPECT_FALSE(other_case < url);
}

// Urls are ordered by the first part that is not the same: the scheme,
// username, password, host, port, path, query, then fragment.
TEST(UrlTest, OrderedByTheFirstPartThatDiffers) {
    const Url url = Url::parse("http://user:pass@example.com:8080/path?a=1#top").value();

    // Each comes after url, in one part.
    for (const char* text : {
             "https://user:pass@example.com:8080/path?a=1#top",
             "http://vser:pass@example.com:8080/path?a=1#top",
             "http://user:qass@example.com:8080/path?a=1#top",
             "http://user:pass@fxample.com:8080/path?a=1#top",
             "http://user:pass@example.com:8081/path?a=1#top",
             "http://user:pass@example.com:8080/qath?a=1#top",
             "http://user:pass@example.com:8080/path?a=2#top",
             "http://user:pass@example.com:8080/path?a=1#uop",
         }) {
        EXPECT_TRUE(comes_before(url, Url::parse(text).value())) << text;
    }
}

TEST(UrlTest, UsernameWithoutPassword) {
    EXPECT_EQ(Url::parse("http://user@example.com/").value().to_string(),
              "http://user@example.com/");
}

// The brackets around a host are taken off, and an IPv6 address is written
// with them, once.
TEST(UrlTest, HostSetWithBrackets) {
    Url url;

    url.set_scheme("http");
    url.set_host("[::1]");
    EXPECT_EQ(url.host(), "::1");
    EXPECT_EQ(url.to_string(), "http://[::1]/");

    url.set_host("[[::1]]");
    EXPECT_EQ(url.host(), "[::1]");
    EXPECT_EQ(url.to_string(), "http://[::1]/");

    url.set_host("[::1");
    EXPECT_EQ(url.host(), "[::1");
    EXPECT_EQ(url.to_string(), "http://[[::1]/");
}

TEST(UrlTest, HostSetWithBracketsNotAroundItAll) {
    Url url;

    for (const char* host : {"[", "[example.com", "example.com]"}) {
        url.set_host(host);
        EXPECT_EQ(url.host(), host);
    }
}

TEST(UrlTest, UnreadableUrlStreamedIn) {
    std::istringstream in_stream("http://[bad/");
    Url url;

    in_stream >> url;

    EXPECT_TRUE(in_stream.fail());
    EXPECT_TRUE(url.host().empty());
}

// Nothing, or an absolute Url that cannot be read, leaves the Url as it was.
TEST(UrlTest, AppendNothing) {
    Url url = Url::parse("http://example.com/a/b").value();

    url.append("");
    EXPECT_EQ(url.to_string(), "http://example.com/a/b");

    url.append("http://[bad/");
    EXPECT_EQ(url.to_string(), "http://example.com/a/b");
}

TEST(UrlTest, AppendToAPathWithoutSlash) {
    Url url;

    url.set_path("page");
    url.append("other");

    EXPECT_EQ(url.path(), "/other");
}

TEST(UrlTest, PortTooLargeToRead) {
    const Url url = Url::parse("http://localhost:99999999999999999999999/").value();

    EXPECT_FALSE(url.port().has_value());
    EXPECT_EQ(url.host(), "localhost");
}

// NOLINTEND(bugprone-unchecked-optional-access)
