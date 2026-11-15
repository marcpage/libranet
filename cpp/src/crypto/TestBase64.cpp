#include <iostream>
#include <cassert>
#include <string>
#include <vector>

#include "Base64.hpp"

using namespace crypto;

void test_encode_string() {
    std::cout << "-> Testing Base64 string encoding... " << std::flush;

    std::string plaintext = "Hello, World!";
    std::string encoded = Base64::encode(plaintext);

    assert(encoded == "SGVsbG8sIFdvcmxkIQ==");

    std::cout << "PASSED\n";
}

void test_encode_bytes() {
    std::cout << "-> Testing Base64 byte vector encoding... " << std::flush;

    std::vector<std::byte> bytes = {
        std::byte{'a'}, std::byte{'b'}, std::byte{'c'}
    };
    std::string encoded = Base64::encode(bytes);

    assert(encoded == "YWJj");

    std::cout << "PASSED\n";
}

void test_decode_valid() {
    std::cout << "-> Testing Base64 successful decoding... " << std::flush;

    std::string encoded = "SGVsbG8sIFdvcmxkIQ==";
    std::vector<std::byte> decoded = Base64::decode(encoded);

    std::string decoded_string;
    decoded_string.reserve(decoded.size());
    for (auto byte_item : decoded) {
        decoded_string.push_back(static_cast<char>(byte_item));
    }

    assert(decoded_string == "Hello, World!");

    std::cout << "PASSED\n";
}

void test_decode_invalid_length() {
    std::cout << "-> Testing Base64 decode with invalid length... " << std::flush;

    // Length not a multiple of 4
    bool exception_thrown = false;
    try {
        [[maybe_unused]] auto decoded_result = Base64::decode("abc");
    } catch (const std::invalid_argument&) {
        exception_thrown = true;
    }

    assert(exception_thrown);

    std::cout << "PASSED\n";
}

void test_decode_invalid_character() {
    std::cout << "-> Testing Base64 decode with invalid character... " << std::flush;

    // Contains '$' which is not part of the Base64 alphabet
    bool exception_thrown = false;
    try {
        [[maybe_unused]] auto decoded_result = Base64::decode("AB$D");
    } catch (const std::invalid_argument&) {
        exception_thrown = true;
    }

    assert(exception_thrown);

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "    Running Base64 Test Suite    \n";

    test_encode_string();
    test_encode_bytes();
    test_decode_valid();
    test_decode_invalid_length();
    test_decode_invalid_character();

    std::cout << "   All Tests Passed Successfully!   \n";
    return 0;
}
