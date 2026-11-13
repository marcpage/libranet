#include <iostream>
#include <cassert>
#include <sstream>
#include <string>
#include <stdexcept>
#include "Request.hpp"

using namespace http;

void test_constructors_and_accessors() {
    std::cout << "-> Testing constructors, setters, and basic accessors... " << std::flush;

    // Default constructor state
    Request req;
    assert(req.method().empty());
    assert(req.uri().empty());
    assert(req.version() == "HTTP/1.1"); // Default version
    assert(req.body().empty());
    assert(req.headers().empty());

    // Setters and getters
    req.set_method("POST");
    req.set_uri("/api/v1/submit");
    req.set_version("HTTP/1.0");
    req.set_body("Hello, Server!");

    assert(req.method() == "POST");
    assert(req.uri() == "/api/v1/submit");
    assert(req.version() == "HTTP/1.0");
    assert(req.body() == "Hello, Server!");

    // Modifying headers through composition
    req.headers().set("Content-Type", "text/plain");
    assert(req.headers().get("content-type") == "text/plain");

    std::cout << "PASSED\n";
}

void test_parse_request_line() {
    std::cout << "-> Testing request-line parsing logic... " << std::flush;

    Request req;

    // Valid request line
    bool success = req.parse_request_line("GET /index.html HTTP/1.1\r\n");
    assert(success);
    assert(req.method() == "GET");
    assert(req.uri() == "/index.html");
    assert(req.version() == "HTTP/1.1");

    // Valid request line with extra spacing
    bool success_spaced = req.parse_request_line("   POST   /submit   HTTP/1.1   ");
    assert(success_spaced);
    assert(req.method() == "POST");
    assert(req.uri() == "/submit");
    assert(req.version() == "HTTP/1.1");

    // Malformed: missing version
    assert(!req.parse_request_line("GET /index.html"));

    // Malformed: missing URI and version
    assert(!req.parse_request_line("GET"));

    // Malformed: empty line
    assert(!req.parse_request_line(""));

    std::cout << "PASSED\n";
}

void test_stream_parsing_and_body() {
    std::cout << "-> Testing full request stream operator parsing (with body)... " << std::flush;

    std::istringstream iss(
        "POST /api/data HTTP/1.1\r\n"
        "Host: api.example.com\r\n"
        "Content-Length: 15\r\n"
        "\r\n"
        "{\"key\":\"value\"}"
    );

    Request req;
    iss >> req;

    assert(!iss.fail());
    assert(req.method() == "POST");
    assert(req.uri() == "/api/data");
    assert(req.version() == "HTTP/1.1");
    assert(req.headers().get("host") == "api.example.com");
    assert(req.headers().get("content-length") == "15");
    assert(req.body() == "{\"key\":\"value\"}");

    std::cout << "PASSED\n";
}

void test_serialization() {
    std::cout << "-> Testing request serialization (to_string)... " << std::flush;

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

    assert(serialized == expected);

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "    Running Request Test Suite      \n";

    test_constructors_and_accessors();
    test_parse_request_line();
    test_stream_parsing_and_body();
    test_serialization();

    std::cout << "   All Tests Passed Successfully!   \n";

    return 0;
}
