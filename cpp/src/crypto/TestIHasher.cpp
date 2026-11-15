#include <iostream>
#include <cassert>
#include <string>
#include <vector>

#include "IHasher.hpp"

using namespace crypto;

void test_metadata() {
    std::cout << "-> Testing digest_size() and block_size()... " << std::flush;

    auto hasher = IHasher::Sha256();
    assert(hasher->digest_size() == 32); // SHA-256 produces 32 bytes
    assert(hasher->block_size() == 64);  // SHA-256 block size is 64 bytes

    std::cout << "PASSED\n";
}

void test_one_shot_and_hex_digest() {
    std::cout << "-> Testing one-shot hashing and hex_digest()... " << std::flush;

    auto hasher = IHasher::Sha256();

    // Known SHA-256 hash for "abc"
    // Expected hex: ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
    std::string hex = hasher->hex_digest(IHasher::Data{
        std::byte{'a'}, std::byte{'b'}, std::byte{'c'}
    });

    assert(hex == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");

    std::cout << "PASSED\n";
}

void test_streaming_updates() {
    std::cout << "-> Testing streaming updates with string_view and Data... " << std::flush;

    auto hasher = IHasher::Sha256();
    hasher->update("abcd");
    hasher->update(IHasher::Data{std::byte{'e'}, std::byte{'f'}});

    // Hash of "abcdef" (hex_digest finalizes and resets)
    std::string hex = hasher->hex_digest();
    assert(hex == "bef57ec7f53a6d40beb640a780a639c83bc29ac8a9816f1fc6c5c6dcd93c4721");

    std::cout << "PASSED\n";
}

void test_reset() {
    std::cout << "-> Testing reset() clears internal state... " << std::flush;

    auto hasher = IHasher::Sha256();
    hasher->update("garbage data");
    hasher->reset();

    // After reset, hashing "abc" should yield the correct "abc" hash, not corrupted state
    hasher->update("abc");
    std::string hex = hasher->hex_digest();

    assert(hex == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");

    std::cout << "PASSED\n";
}

void test_clone() {
    std::cout << "-> Testing clone() duplicates exact state independently... " << std::flush;

    auto hasher1 = IHasher::Sha256();
    hasher1->update("hello");

    // Clone mid-stream (cloning preserves current active context without affecting hasher1)
    auto hasher2 = hasher1->clone();

    // Continue hasher1 with " world" -> "hello world"
    hasher1->update(" world");
    std::string hex1 = hasher1->hex_digest();

    // Continue hasher2 with " there" -> "hello there"
    hasher2->update(" there");
    std::string hex2 = hasher2->hex_digest();

    // SHA-256("hello world")
    assert(hex1 == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9");
    // SHA-256("hello there")
    assert(hex2 == "12998c017066eb0d2a70b94e6ed3192985855ce390f321bbdb832022888bd251");

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "    Running IHasher Test Suite    \n";

    test_metadata();
    test_one_shot_and_hex_digest();
    test_streaming_updates();
    test_reset();
    test_clone();

    std::cout << "   All Tests Passed Successfully!   \n";
    return 0;
}
