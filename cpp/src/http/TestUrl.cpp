#include <iostream>
#include <cassert>
#include <sstream>
#include <string>

#include "Url.hpp"

using namespace http;

void test_constructors_and_basic_accessors() {
    std::cout << "-> Testing constructors, setters, and basic accessors... " << std::flush;

    // Default constructor (empty state)
    Url url;
    assert(url.scheme().empty());
    assert(url.host().empty());
    assert(!url.port().has_value());
    assert(url.path() == "/");
    assert(url.username().empty());
    assert(url.password().empty());
    assert(url.fragment().empty());
    assert(url.query().empty());

    // Basic setters and accessors
    url.set_scheme("https");
    url.set_host("example.com");
    url.set_port(8080);
    url.set_path("/api/v1/resource");
    url.set_username("admin");
    url.set_password("secret");
    url.set_fragment("section2");

    assert(url.scheme() == "https");
    assert(url.host() == "example.com");
    assert(url.port() == 8080);
    assert(url.port().has_value());
    assert(url.port() == 8080);
    assert(url.path() == "/api/v1/resource");
    assert(url.username() == "admin");
    assert(url.password() == "secret");
    assert(url.fragment() == "section2");

    std::cout << "PASSED\n";
}

void test_url_parsing() {
    std::cout << "-> Testing static URL parsing factory method... " << std::flush;

    // Comprehensive URL parsing test
    auto parsed = Url::parse("https://john:doe@sub.example.com:9000/path/to/page?foo=bar&baz=123#anchor");
    assert(parsed.has_value());

    const auto& url = parsed.value();
    assert(url.scheme() == "https");
    assert(url.username() == "john");
    assert(url.password() == "doe");
    assert(url.host() == "sub.example.com");
    assert(url.port().value() == 9000);
    assert(url.port().has_value());
    assert(url.path() == "/path/to/page");
    assert(url.fragment() == "anchor");

    auto foo_val = url.get_query("foo");
    assert(foo_val.has_value() && foo_val == "bar");

    auto baz_val = url.get_query("baz");
    assert(baz_val.has_value() && baz_val == "123");

    // Minimal URL parsing test (defaults and implicit paths)
    auto minimal = Url::parse("http://example.com");
    assert(minimal.has_value());
    assert(minimal->scheme() == "http");
    assert(minimal->host() == "example.com");
    assert(minimal->path() == "/");
    assert(!minimal->port().has_value());

    // Invalid or empty URL checks
    assert(!Url::parse("").has_value());

    std::cout << "PASSED\n";
}

void test_port_validation_and_effective_ports() {
    std::cout << "-> Testing port validation and effective port resolution... " << std::flush;

    // 1. Valid explicit port boundaries
    auto max_port_url = Url::parse("http://localhost:65535");
    assert(max_port_url.has_value());
    assert(max_port_url->port().has_value());
    assert(max_port_url->port() == 65535);
    assert(max_port_url->effective_port() == 65535);

    // 2. Out of range port (> 65535)
    auto overflow_port_url = Url::parse("http://localhost:65536");
    assert(overflow_port_url.has_value());
    assert(!overflow_port_url->port().has_value());

    // 3. Non-numeric port string
    auto invalid_port_url = Url::parse("http://localhost:abc");
    assert(invalid_port_url.has_value());
    assert(!invalid_port_url->port().has_value());

    // 4. Programmatic port number setter
    Url url;
    url.set_port(443);
    assert(url.port().has_value() && url.port() == 443);

    // 5. Effective port fallback resolution based on scheme
    Url http_url;
    http_url.set_scheme("http");
    assert(!http_url.port().has_value());
    assert(http_url.effective_port().has_value() && http_url.effective_port() == 80);

    Url https_url;
    https_url.set_scheme("HTTPS");
    assert(https_url.effective_port().has_value() && https_url.effective_port() == 443);

    Url ftp_url;
    ftp_url.set_scheme("ftp");
    assert(ftp_url.effective_port().has_value() && ftp_url.effective_port() == 21);

    Url ws_url;
    ws_url.set_scheme("ws");
    assert(ws_url.effective_port().has_value() && ws_url.effective_port() == 80);

    Url wss_url;
    wss_url.set_scheme("wss");
    assert(wss_url.effective_port().has_value() && wss_url.effective_port() == 443);

    Url unknown_url;
    unknown_url.set_scheme("custom");
    assert(!unknown_url.effective_port().has_value());

    std::cout << "PASSED\n";
}

void test_url_encoding_and_decoding() {
    std::cout << "-> Testing static context-aware encode and decode methods... " << std::flush;

    std::string raw = "hello world/test?foo=bar+baz";

    // 1. UriComponent Context
    std::string encoded_uri = Url::encode(raw, Url::EncodeContext::UriComponent);
    assert(encoded_uri == "hello%20world%2Ftest%3Ffoo%3Dbar%2Bbaz");
    assert(Url::decode(encoded_uri, Url::EncodeContext::UriComponent) == raw);

    // 2. FormUrl Context
    std::string encoded_form = Url::encode(raw, Url::EncodeContext::FormUrl);
    assert(encoded_form == "hello+world%2Ftest%3Ffoo%3Dbar%2Bbaz");
    assert(Url::decode(encoded_form, Url::EncodeContext::FormUrl) == raw);

    // 3. Custom allowed characters overload test
    std::string custom_encoded = Url::encode("path/to/resource", "/", false);
    assert(custom_encoded == "path/to/resource");

    std::cout << "PASSED\n";
}

void test_transparent_component_encoding() {
    std::cout << "-> Testing transparent component encoding & decoding... " << std::flush;

    Url url;
    url.set_path("/my folder/sub folder/file.txt");
    url.set_username("john doe");
    url.set_password("p@ss word");
    url.set_fragment("section 3?query=true");
    url.set_query_parameters({
        {"search", "modern c++"},
        {"tag", "networking"}
    });

    // 1. Verify getters return decoded, human-readable values transparently
    assert(url.path() == "/my folder/sub folder/file.txt");
    assert(url.username() == "john doe");
    assert(url.password() == "p@ss word");
    assert(url.fragment() == "section 3?query=true");

    // 2. Verify optional accesses for query parameters
    auto search_val = url.get_query("search");
    assert(search_val.has_value() && search_val.value() == "modern c++");

    auto missing_val = url.get_query("nonexistent");
    assert(!missing_val.has_value());
    assert(missing_val.value_or("default") == "default");

    // 3. Verify case-insensitive comparison operators
    Url url1 = Url::parse("HTTPS://Example.COM:443/path").value();
    Url url2 = Url::parse("https://example.com:443/path").value();
    assert(url1 == url2);
    assert(!(url1 < url2));

    std::cout << "PASSED\n";
}

void test_url_append() {
    std::cout << "-> Testing relative URL append/resolution method... " << std::flush;

    auto base = Url::parse("https://example.com/api/v1/posts");
    assert(base.has_value());

    auto& url_ref = base.value();

    // 1. Append relative path segment
    url_ref.append("123/comments");
    assert(url_ref.to_string() == "https://example.com/api/v1/123/comments");

    // 2. Append absolute path starting with '/'
    url_ref.append("/health");
    assert(url_ref.to_string() == "https://example.com/health");

    // 3. Append query parameters and fragments
    url_ref.append("?verbose=true#status");
    assert(url_ref.to_string() == "https://example.com/health?verbose=true#status");

    // 4. Append an absolute URL
    url_ref.append("http://other.org/resource");
    assert(url_ref.to_string() == "http://other.org/resource");

    std::cout << "PASSED\n";
}

void test_serialization_and_reconstruction() {
    std::cout << "-> Testing serialization (to_string) and round-trip fidelity... "
            << std::flush;

    std::string original_str = "https://user:pass@example.com:8443/search?q=cpp#top";
    auto parsed = Url::parse(original_str);
    assert(parsed.has_value());

    std::string serialized = parsed.value().to_string();
    assert(serialized == original_str);

    std::cout << "PASSED\n";
}

void test_stream_operators_and_parsing() {
    std::cout << "-> Testing stream I/O operators for Url... " << std::flush;

    auto url_opt = Url::parse("ftp://files.example.com/pub/file.txt");
    assert(url_opt.has_value());

    std::ostringstream oss;
    oss << url_opt.value();
    assert(oss.str() == "ftp://files.example.com/pub/file.txt");

    std::istringstream iss("http://test.org/index.html?ref=home");
    Url stream_url;
    iss >> stream_url;

    assert(!iss.fail());
    assert(stream_url.scheme() == "http");
    assert(stream_url.host() == "test.org");
    assert(stream_url.path() == "/index.html");

    auto ref_val = stream_url.get_query("ref");
    assert(ref_val.has_value() && ref_val == "home");

    std::istringstream bad_iss("");
    Url bad_url;
    bad_iss >> bad_url;
    assert(bad_iss.fail());

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "      Running Url Test Suite            \n";

    test_constructors_and_basic_accessors();
    test_url_parsing();
    test_port_validation_and_effective_ports();
    test_url_encoding_and_decoding();
    test_transparent_component_encoding();
    test_url_append();
    test_serialization_and_reconstruction();
    test_stream_operators_and_parsing();

    std::cout << "   All Tests Passed Successfully!      \n";

    return 0;
}
