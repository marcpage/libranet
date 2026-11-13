#include <gtest/gtest.h>
#include <sstream>
#include <string>

#include "Url.hpp"

using namespace http;

TEST(UrlTest, ConstructorsAndBasicAccessors) {
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
    url.set_port(8080);
    url.set_path("/api/v1/resource");
    url.set_username("admin");
    url.set_password("secret");
    url.set_fragment("section2");

    EXPECT_EQ(url.scheme(), "https");
    EXPECT_EQ(url.host(), "example.com");
    EXPECT_EQ(url.port(), 8080);
    EXPECT_TRUE(url.port().has_value());
    EXPECT_EQ(url.port(), 8080);
    EXPECT_EQ(url.path(), "/api/v1/resource");
    EXPECT_EQ(url.username(), "admin");
    EXPECT_EQ(url.password(), "secret");
    EXPECT_EQ(url.fragment(), "section2");
}

TEST(UrlTest, UrlParsing) {
    // Comprehensive URL parsing test
    auto parsed = Url::parse("https://john:doe@sub.example.com:9000/path/to/page?foo=bar&baz=123#anchor");
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

    auto foo_val = url.get_query("foo");
    EXPECT_TRUE(foo_val.has_value());
    EXPECT_EQ(foo_val, "bar");

    auto baz_val = url.get_query("baz");
    EXPECT_TRUE(baz_val.has_value());
    EXPECT_EQ(baz_val, "123");

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
    EXPECT_EQ(max_port_url->port(), 65535);
    EXPECT_EQ(max_port_url->effective_port(), 65535);

    // 2. Out of range port (> 65535)
    auto overflow_port_url = Url::parse("http://localhost:65536");
    ASSERT_TRUE(overflow_port_url.has_value());
    EXPECT_FALSE(overflow_port_url->port().has_value());

    // 3. Non-numeric port string
    auto invalid_port_url = Url::parse("http://localhost:abc");
    ASSERT_TRUE(invalid_port_url.has_value());
    EXPECT_FALSE(invalid_port_url->port().has_value());

    // 4. Programmatic port number setter
    Url url;
    url.set_port(443);
    EXPECT_TRUE(url.port().has_value());
    EXPECT_EQ(url.port(), 443);

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
    std::string raw = "hello world/test?foo=bar+baz";

    // 1. UriComponent Context
    std::string encoded_uri = Url::encode(raw, Url::EncodeContext::UriComponent);
    EXPECT_EQ(encoded_uri, "hello%20world%2Ftest%3Ffoo%3Dbar%2Bbaz");
    EXPECT_EQ(Url::decode(encoded_uri, Url::EncodeContext::UriComponent), raw);

    // 2. FormUrl Context
    std::string encoded_form = Url::encode(raw, Url::EncodeContext::FormUrl);
    EXPECT_EQ(encoded_form, "hello+world%2Ftest%3Ffoo%3Dbar%2Bbaz");
    EXPECT_EQ(Url::decode(encoded_form, Url::EncodeContext::FormUrl), raw);

    // 3. Custom allowed characters overload test
    std::string custom_encoded = Url::encode("path/to/resource", "/", false);
    EXPECT_EQ(custom_encoded, "path/to/resource");
}

TEST(UrlTest, TransparentComponentEncoding) {
    Url url;
    url.set_path("/my folder/sub folder/file.txt");
    url.set_username("john doe");
    url.set_password("p@ss word");
    url.set_fragment("section 3?query=true");
    url.set_query_parameters({{"search", "modern c++"},
                              {"tag", "networking"}});

    // 1. Verify getters return decoded, human-readable values transparently
    EXPECT_EQ(url.path(), "/my folder/sub folder/file.txt");
    EXPECT_EQ(url.username(), "john doe");
    EXPECT_EQ(url.password(), "p@ss word");
    EXPECT_EQ(url.fragment(), "section 3?query=true");

    // 2. Verify optional accesses for query parameters
    auto search_val = url.get_query("search");
    ASSERT_TRUE(search_val.has_value());
    EXPECT_EQ(search_val.value(), "modern c++");

    auto missing_val = url.get_query("nonexistent");
    EXPECT_FALSE(missing_val.has_value());
    EXPECT_EQ(missing_val.value_or("default"), "default");

    // 3. Verify case-insensitive comparison operators
    Url url1 = Url::parse("HTTPS://Example.COM:443/path").value();
    Url url2 = Url::parse("https://example.com:443/path").value();
    EXPECT_TRUE(url1 == url2);
    EXPECT_FALSE(url1 < url2);
}

TEST(UrlTest, UrlAppend) {
    auto base = Url::parse("https://example.com/api/v1/posts");
    ASSERT_TRUE(base.has_value());

    auto& url_ref = base.value();

    // 1. Append relative path segment
    url_ref.append("123/comments");
    EXPECT_EQ(url_ref.to_string(), "https://example.com/api/v1/123/comments");

    // 2. Append absolute path starting with '/'
    url_ref.append("/health");
    EXPECT_EQ(url_ref.to_string(), "https://example.com/health");

    // 3. Append query parameters and fragments
    url_ref.append("?verbose=true#status");
    EXPECT_EQ(url_ref.to_string(), "https://example.com/health?verbose=true#status");

    // 4. Append an absolute URL
    url_ref.append("http://other.org/resource");
    EXPECT_EQ(url_ref.to_string(), "http://other.org/resource");
}

TEST(UrlTest, SerializationAndReconstruction) {
    std::string original_str = "https://user:pass@example.com:8443/search?q=cpp#top";
    auto parsed = Url::parse(original_str);
    ASSERT_TRUE(parsed.has_value());

    std::string serialized = parsed.value().to_string();
    EXPECT_EQ(serialized, original_str);
}

TEST(UrlTest, StreamOperatorsAndParsing) {
    auto url_opt = Url::parse("ftp://files.example.com/pub/file.txt");
    ASSERT_TRUE(url_opt.has_value());

    std::ostringstream oss;
    oss << url_opt.value();
    EXPECT_EQ(oss.str(), "ftp://files.example.com/pub/file.txt");

    std::istringstream iss("http://test.org/index.html?ref=home");
    Url stream_url;
    iss >> stream_url;

    EXPECT_FALSE(iss.fail());
    EXPECT_EQ(stream_url.scheme(), "http");
    EXPECT_EQ(stream_url.host(), "test.org");
    EXPECT_EQ(stream_url.path(), "/index.html");

    auto ref_val = stream_url.get_query("ref");
    EXPECT_TRUE(ref_val.has_value());
    EXPECT_EQ(ref_val, "home");

    std::istringstream bad_iss("");
    Url bad_url;
    bad_iss >> bad_url;
    EXPECT_TRUE(bad_iss.fail());
}
