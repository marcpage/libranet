#include <iostream>
#include <cassert>
#include <sstream>
#include <string>
#include <stdexcept>

#include "Response.hpp"

using namespace http;

void test_constructors_and_accessors() {
    std::cout << "-> Testing constructors, setters, and basic accessors... " << std::flush;

    // Default constructor state (HTTP/1.1 200 OK)
    Response res;
    assert(res.version() == "HTTP/1.1");
    assert(res.status_code() == 200);
    assert(res.reason_phrase() == "OK");
    assert(res.body().empty());
    assert(res.headers().empty());

    // Setters and getters
    res.set_version("HTTP/1.0");
    res.set_status_code(404);
    res.set_reason_phrase("Not Found");
    res.set_body("Resource missing");

    assert(res.version() == "HTTP/1.0");
    assert(res.status_code() == 404);
    assert(res.reason_phrase() == "Not Found");
    assert(res.body() == "Resource missing");

    // Modifying headers through composition
    res.headers().set("Content-Type", "text/plain");
    assert(res.headers().get("content-type") == "text/plain");

    std::cout << "PASSED\n";
}

void test_parse_status_line() {
    std::cout << "-> Testing status-line parsing logic... " << std::flush;

    Response res;

    // Valid status line with reason phrase
    bool success = res.parse_status_line("HTTP/1.1 200 OK\r\n");
    assert(success);
    assert(res.version() == "HTTP/1.1");
    assert(res.status_code() == 200);
    assert(res.reason_phrase() == "OK");

    // Valid status line without reason phrase
    bool success_no_reason = res.parse_status_line("HTTP/1.1 500");
    assert(success_no_reason);
    assert(res.version() == "HTTP/1.1");
    assert(res.status_code() == 500);
    assert(res.reason_phrase().empty());

    // Valid status line with extra spacing
    bool success_spaced = res.parse_status_line("   HTTP/1.1   403   Forbidden   ");
    assert(success_spaced);
    assert(res.version() == "HTTP/1.1");
    assert(res.status_code() == 403);
    assert(res.reason_phrase() == "Forbidden");

    // Malformed: non-numeric status code
    assert(!res.parse_status_line("HTTP/1.1 ABC OK"));

    // Malformed: missing status code
    assert(!res.parse_status_line("HTTP/1.1"));

    // Malformed: empty line
    assert(!res.parse_status_line(""));

    std::cout << "PASSED\n";
}

void test_stream_parsing_and_body() {
    std::cout << "-> Testing full response stream operator parsing (with body)... " << std::flush;

    std::istringstream iss(
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/plain\r\n"
        "Content-Length: 12\r\n"
        "\r\n"
        "Hello World!"
    );

    Response res;
    iss >> res;

    assert(!iss.fail());
    assert(res.version() == "HTTP/1.1");
    assert(res.status_code() == 200);
    assert(res.reason_phrase() == "OK");
    assert(res.headers().get("content-type") == "text/plain");
    assert(res.headers().get("content-length") == "12");
    assert(res.body() == "Hello World!");

    std::cout << "PASSED\n";
}

void test_serialization() {
    std::cout << "-> Testing response serialization (to_string)... " << std::flush;

    Response res;
    res.set_version("HTTP/1.1");
    res.set_status_code(201);
    res.set_reason_phrase("Created");
    res.headers().set("Connection", "close");

    std::string serialized = res.to_string();

    // Status line + headers + blank line + body (empty)
    std::string expected =
        "HTTP/1.1 201 Created\r\n"
        "Connection: close\r\n"
        "\r\n";

    assert(serialized == expected);

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "    Running Response Test Suite     \n";

    test_constructors_and_accessors();
    test_parse_status_line();
    test_stream_parsing_and_body();
    test_serialization();

    std::cout << "   All Tests Passed Successfully!   \n";

    return 0;
}
