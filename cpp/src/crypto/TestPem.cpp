#include <iostream>
#include <cassert>
#include <string>
#include <vector>
#include <stdexcept>

#include "Pem.hpp"

using namespace crypto;

void test_wrap_basic() {
    std::cout << "-> Testing Pem wrap basic formatting... " << std::flush;

    std::vector<std::byte> data = {
        std::byte{'a'}, std::byte{'b'}, std::byte{'c'}
    };

    std::string pem = Pem::wrap("PUBLIC KEY", data);

    std::string expected =
        "-----BEGIN PUBLIC KEY-----\n"
        "YWJj\n"
        "-----END PUBLIC KEY-----\n";

    assert(pem == expected);

    std::cout << "PASSED\n";
}

void test_wrap_line_wrapping() {
    std::cout << "-> Testing Pem wrap line length limits... " << std::flush;

    // Create a vector of 64 bytes to ensure Base64 output exceeds standard 64-char line width
    std::vector<std::byte> data(64, std::byte{'A'});
    std::string pem = Pem::wrap("PRIVATE KEY", data);

    assert(pem.rfind("-----BEGIN PRIVATE KEY-----\n", 0) == 0);
    assert(pem.find("-----END PRIVATE KEY-----\n") != std::string::npos);

    std::cout << "PASSED\n";
}

void test_unwrap_valid() {
    std::cout << "-> Testing Pem successful unwrap... " << std::flush;

    std::string pem =
        "-----BEGIN PUBLIC KEY-----\n"
        "YWJj\n"
        "-----END PUBLIC KEY-----\n";

    auto [label, data] = Pem::unwrap(pem);

    assert(label == "PUBLIC KEY");
    assert(data.size() == 3);
    assert(data[0] == std::byte{'a'});
    assert(data[1] == std::byte{'b'});
    assert(data[2] == std::byte{'c'});

    std::cout << "PASSED\n";
}

void test_unwrap_with_whitespace() {
    std::cout << "-> Testing Pem unwrap with excess whitespace... " << std::flush;

    std::string pem =
        "\n  -----BEGIN RSA PRIVATE KEY-----\r\n"
        " YWJj \n"
        "-----END RSA PRIVATE KEY-----  \n\n";

    auto [label, data] = Pem::unwrap(pem);

    assert(label == "RSA PRIVATE KEY");
    assert(data.size() == 3);

    std::cout << "PASSED\n";
}

void test_unwrap_missing_header() {
    std::cout << "-> Testing Pem unwrap missing header... " << std::flush;

    std::string pem =
        "YWJj\n"
        "-----END PUBLIC KEY-----\n";

    bool exception_thrown = false;
    try {
        [[maybe_unused]] auto result = Pem::unwrap(pem);
    } catch (const std::invalid_argument&) {
        exception_thrown = true;
    }

    assert(exception_thrown);

    std::cout << "PASSED\n";
}

void test_unwrap_missing_footer() {
    std::cout << "-> Testing Pem unwrap missing footer... " << std::flush;

    std::string pem =
        "-----BEGIN PUBLIC KEY-----\n"
        "YWJj\n";

    bool exception_thrown = false;
    try {
        [[maybe_unused]] auto result = Pem::unwrap(pem);
    } catch (const std::invalid_argument&) {
        exception_thrown = true;
    }

    assert(exception_thrown);

    std::cout << "PASSED\n";
}

void test_unwrap_mismatched_label() {
    std::cout << "-> Testing Pem unwrap mismatched header/footer labels... " << std::flush;

    std::string pem =
        "-----BEGIN PUBLIC KEY-----\n"
        "YWJj\n"
        "-----END PRIVATE KEY-----\n";

    bool exception_thrown = false;
    try {
        [[maybe_unused]] auto result = Pem::unwrap(pem);
    } catch (const std::invalid_argument&) {
        exception_thrown = true;
    }

    assert(exception_thrown);

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "    Running Pem Test Suite    \n";

    test_wrap_basic();
    test_wrap_line_wrapping();
    test_unwrap_valid();
    test_unwrap_with_whitespace();
    test_unwrap_missing_header();
    test_unwrap_missing_footer();
    test_unwrap_mismatched_label();

    std::cout << "   All Tests Passed Successfully!   \n";
    return 0;
}
