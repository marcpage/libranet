#include <iostream>
#include <cassert>
#include <string>
#include <vector>

#include "IMultiKey.hpp"
#include "ISignKey.hpp"
#include "IEncryptKey.hpp"

using namespace crypto;

void test_rsa_key_generation() {
    std::cout << "-> Testing RSA key pair generation (2048-bit)... " << std::flush;

    auto multi_key = IMultiKey::Rsa(2048);
    assert(multi_key != nullptr);
    assert(multi_key->id() == "rsa");

    auto signer = multi_key->RsaPssSha256();
    assert(signer != nullptr);
    assert(signer->has_private_key() == true);

    auto encryptor = multi_key->RsaOaepSha256();
    assert(encryptor != nullptr);

    std::cout << "PASSED\n";
}

void test_rsa_signing_and_verification() {
    std::cout << "-> Testing RSA PSS SHA-256 signing and verification... " << std::flush;

    auto multi_key = IMultiKey::Rsa(2048);
    auto signer = multi_key->RsaPssSha256();

    std::string message = "Libranet Cryptographic Library Test Message";
    auto signature = signer->sign(message);
    assert(!signature.empty());

    bool is_valid = signer->verify(message, signature);
    assert(is_valid == true);

    // Test verification failure with tampered message
    bool is_invalid = signer->verify("Tampered Message", signature);
    assert(is_invalid == false);

    std::cout << "PASSED\n";
}

void test_rsa_encryption_and_decryption() {
    std::cout << "-> Testing RSA OAEP SHA-256 encryption and decryption... " << std::flush;

    auto multi_key = IMultiKey::Rsa(2048);
    auto encryptor = multi_key->RsaOaepSha256();

    std::string plaintext = "Confidential Payload";
    // RSA-OAEP ignores initialization vectors, pass empty vector
    IEncryptKey::InitializationVector iv;

    auto ciphertext = encryptor->encrypt(plaintext, iv);
    assert(!ciphertext.empty());

    std::string decrypted = encryptor->decrypt(ciphertext, iv);
    assert(decrypted == plaintext);

    std::cout << "PASSED\n";
}

void test_pem_roundtrip() {
    std::cout << "-> Testing PEM export and re-import (from_pem)... " << std::flush;

    auto original_key = IMultiKey::Rsa(2048);
    std::string private_pem = original_key->private_pem();
    std::string public_pem = original_key->public_pem();

    assert(!private_pem.empty());
    assert(!public_pem.empty());

    // Import from private PEM
    auto imported_from_private = IMultiKey::from_pem(private_pem);
    assert(imported_from_private != nullptr);

    auto imported_signer = imported_from_private->RsaPssSha256();
    assert(imported_signer->has_private_key() == true);

    std::string message = "PEM Roundtrip Message";
    auto signature = imported_signer->sign(message);
    assert(imported_signer->verify(message, signature) == true);

    // Import from public PEM
    auto imported_from_public = IMultiKey::from_pem(public_pem);
    assert(imported_from_public != nullptr);

    auto imported_verifier = imported_from_public->RsaPssSha256();
    assert(imported_verifier->has_private_key() == false);
    assert(imported_verifier->verify(message, signature) == true);

    std::cout << "PASSED\n";
}

void test_invalid_pem() {
    std::cout << "-> Testing invalid PEM handling... " << std::flush;

    bool exception_thrown = false;
    try {
        [[maybe_unused]] auto invalid_key = IMultiKey::from_pem("-----BEGIN GARBAGE-----\nYWJj\n-----END GARBAGE-----\n");
    } catch (const std::exception&) {
        exception_thrown = true;
    }

    assert(exception_thrown);

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "    Running IMultiKey Test Suite    \n";

    test_rsa_key_generation();
    test_rsa_signing_and_verification();
    test_rsa_encryption_and_decryption();
    test_pem_roundtrip();
    test_invalid_pem();

    std::cout << "   All Tests Passed Successfully!   \n";
    return 0;
}
