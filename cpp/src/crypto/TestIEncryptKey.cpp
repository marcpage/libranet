#include <iostream>
#include <cassert>
#include <string>
#include <vector>

#include "IEncryptKey.hpp"
#include "AesMacos.hpp"

using namespace crypto;

void test_aes_key_validation() {
    std::cout << "-> Testing AES key size validation... " << std::flush;

    // Valid key sizes: 16 (128-bit), 24 (192-bit), 32 (32 bytes / 256-bit)
    IEncryptKey::Data valid_key_128(16, std::byte{0x01});
    IEncryptKey::Data valid_key_192(24, std::byte{0x02});
    IEncryptKey::Data valid_key_256(32, std::byte{0x03});

    bool threw = false;
    try {
        auto key = IEncryptKey::Aes(valid_key_128, IEncryptKey::AesMode::Cbc, IEncryptKey::AesPadding::Pkcs7);
    } catch (...) {
        threw = true;
    }
    assert(!threw);

    try {
        auto key = IEncryptKey::Aes(valid_key_192, IEncryptKey::AesMode::Ctr, IEncryptKey::AesPadding::None);
    } catch (...) {
        threw = true;
    }
    assert(!threw);

    try {
        auto key = IEncryptKey::Aes(valid_key_256, IEncryptKey::AesMode::Cbc, IEncryptKey::AesPadding::Pkcs7);
    } catch (...) {
        threw = true;
    }
    assert(!threw);

    // Invalid key size (e.g., 15 bytes)
    IEncryptKey::Data invalid_key(15, std::byte{0x00});
    threw = false;
    try {
        auto key = IEncryptKey::Aes(invalid_key, IEncryptKey::AesMode::Cbc, IEncryptKey::AesPadding::Pkcs7);
    } catch (const std::invalid_argument&) {
        threw = true;
    }
    assert(threw);

    std::cout << "PASSED\n";
}

void test_aes_cbc_pkcs7_roundtrip() {
    std::cout << "-> Testing AES-CBC with PKCS7 padding encryption/decryption roundtrip... " << std::flush;

    // 256-bit key (32 bytes)
    IEncryptKey::Data key(32, std::byte{0x42});
    // 128-bit IV (16 bytes)
    IEncryptKey::InitializationVector iv(16, std::byte{0x13});

    auto aes = IEncryptKey::Aes(key, IEncryptKey::AesMode::Cbc, IEncryptKey::AesPadding::Pkcs7);

    std::string plaintext = "Hello, Libranet cryptographic library!";

    auto ciphertext = aes->encrypt(plaintext, iv);
    std::string decrypted = aes->decrypt(ciphertext, iv);

    assert(decrypted == plaintext);

    std::cout << "PASSED\n";
}

void test_aes_ctr_nopadding_roundtrip() {
    std::cout << "-> Testing AES-CTR with NoPadding encryption/decryption roundtrip... " << std::flush;

    // 128-bit key (16 bytes)
    IEncryptKey::Data key(16, std::byte{0x77});
    // 128-bit IV / Counter block (16 bytes)
    IEncryptKey::InitializationVector iv(16, std::byte{0x55});

    auto aes = IEncryptKey::Aes(key, IEncryptKey::AesMode::Ctr, IEncryptKey::AesPadding::None);

    std::string plaintext = "Stream cipher test payload using CTR mode.";

    auto ciphertext = aes->encrypt(plaintext, iv);
    std::string decrypted = aes->decrypt(ciphertext, iv);

    assert(decrypted == plaintext);

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "    Running IEncryptKey Test Suite    \n";

    test_aes_key_validation();
    test_aes_cbc_pkcs7_roundtrip();
    test_aes_ctr_nopadding_roundtrip();

    std::cout << "   All Tests Passed Successfully!   \n";
    return 0;
}
