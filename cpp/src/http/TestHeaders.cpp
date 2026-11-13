#include <iostream>
#include <cassert>
#include <sstream>
#include <string>
#include <stdexcept>
#include "Headers.hpp"

using namespace http;

void test_constructors_and_basic_accessors() {
    std::cout << "-> Testing constructors, setters, and basic accessors... " << std::flush;

    // Default constructor (empty state)
    Headers headers;
    assert(headers.empty());
    assert(headers.size() == 0);
    assert(!headers.has("Content-Type"));

    // Set and case-insensitive retrieval
    headers.set("Content-Type", "application/json");
    assert(!headers.empty());
    assert(headers.size() == 1);
    assert(headers.has("content-type")); // Case-insensitive check
    assert(headers.has("CONTENT-TYPE"));

    auto val = headers.get("CONTENT-TYPE");
    assert(val.has_value());
    assert(val.value() == "application/json");

    // Overloaded get with default value
    assert(headers.get("X-Missing", "default-val") == "default-val");
    assert(headers.get("Content-Type", "default-val") == "application/json");

    // Removal and clearing
    headers.remove("content-type");
    assert(headers.empty());

    headers.set("Host", "localhost");
    headers.set("Connection", "keep-alive");
    assert(headers.size() == 2);
    headers.clear();
    assert(headers.empty());

    std::cout << "PASSED\n";
}

void test_equality_and_serialization() {
    std::cout << "-> Testing equality operators and serialization... " << std::flush;

    Headers h1;
    h1.set("Server", "CustomCppServer");
    h1.set("Content-Length", "1024");

    Headers h2;
    h2.set("content-length", "1024"); // Key storage order / capitalization difference check if map handles it
    h2.set("server", "CustomCppServer");

    assert(h1 == h2);

    h2.set("X-Extra", "foo");
    assert(h1 != h2);

    // Test serialization output format
    std::string serialized = h1.to_string();
    // Headers are stored in sorted order due to std::map
    std::string expected = "Content-Length: 1024\r\nServer: CustomCppServer\r\n\r\n";
    assert(serialized == expected);

    std::cout << "PASSED\n";
}

void test_stream_operators_and_parsing() {
    std::cout << "-> Testing stream I/O and standard header parsing... " << std::flush;

    // Valid header block input stream
    std::istringstream iss(
        "Host: example.com\r\n"
        "User-Agent: CppClient/1.0\r\n"
        "Accept: */*\r\n"
        "\r\n"
    );

    Headers parsed_headers;
    iss >> parsed_headers;

    assert(!iss.fail());
    assert(parsed_headers.size() == 3);
    assert(parsed_headers.get("host") == "example.com");
    assert(parsed_headers.get("user-agent") == "CppClient/1.0");
    assert(parsed_headers.get("accept") == "*/*");

    std::cout << "PASSED\n";
}

void test_continuation_lines_and_edge_cases() {
    std::cout << "-> Testing continuation lines and malformed header exceptions... " << std::flush;

    // Test header folding / continuation lines (OBS-fold)
    std::istringstream folding_iss(
        "Subject: This is a long\r\n"
        " continuation line header\r\n"
        "\r\n"
    );

    Headers folded_headers;
    folding_iss >> folded_headers;
    assert(!folding_iss.fail());
    assert(folded_headers.get("subject") == "This is a long continuation line header");

    // Malformed header exception: Missing colon
    bool caught_missing_colon = false;
    try {
        std::istringstream bad_iss("MalformedHeaderWithoutColon\r\n\r\n");
        Headers dummy;
        bad_iss >> dummy;
    } catch (const std::runtime_error&) {
        caught_missing_colon = true;
    }
    assert(caught_missing_colon);

    // Malformed header exception: Continuation line without preceding header
    bool caught_orphan_continuation = false;
    try {
        std::istringstream bad_iss(" Leading continuation line\r\n\r\n");
        Headers dummy;
        bad_iss >> dummy;
    } catch (const std::runtime_error&) {
        caught_orphan_continuation = true;
    }
    assert(caught_orphan_continuation);

    std::cout << "PASSED\n";
}

void test_helper_utilities() {
    std::cout << "-> Testing standalone helper utility functions... " << std::flush;

    // Trim trailing carriage return
    assert(Headers::trim_trailing_cr("hello\r") == "hello");
    assert(Headers::trim_trailing_cr("hello") == "hello");
    assert(Headers::trim_trailing_cr("") == "");

    // Trim whitespace
    assert(Headers::trim_whitespace("   trimmed   ") == "trimmed");
    assert(Headers::trim_whitespace("\t\tval") == "val");

    // Continuation checker
    assert(Headers::is_continuation_line(" leading space"));
    assert(Headers::is_continuation_line("\tleading tab"));
    assert(!Headers::is_continuation_line("Normal-Header: value"));

    // Parse single header line
    std::string key, value;
    bool success = Headers::parse_header_line("Content-Type:   text/html ", key, value);
    assert(success);
    assert(key == "Content-Type");
    assert(value == "text/html");

    bool fail_success = Headers::parse_header_line("InvalidLine", key, value);
    assert(!fail_success);

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "    Running Headers Test Suite      \n";

    test_constructors_and_basic_accessors();
    test_equality_and_serialization();
    test_stream_operators_and_parsing();
    test_continuation_lines_and_edge_cases();
    test_helper_utilities();

    std::cout << "   All Tests Passed Successfully!   \n";

    return 0;
}
