#include <iostream>
#include <cassert>
#include <string>
#include <vector>

#include "ISignKey.hpp"
#include "Ed25519.hpp"

using namespace crypto;

void test_ed25519_generation_and_signing() {
    std::cout << "-> Testing Ed25519 key generation, signing, and verification... " << std::flush;

    auto signer = ISignKey::Ed25519();
    assert(signer->has_private_key() == true);
    assert(!signer->id().empty());

    std::string message = "Libranet Ed25519 digital signature test message.";
    auto signature = signer->sign(message);

    // Ed25519 signatures are always exactly 64 bytes
    assert(signature.size() == 64);

    // Verify with the same key
    bool valid = signer->verify(message, signature);
    assert(valid == true);

    // Verify with modified message should fail
    bool invalid_msg = signer->verify("Tampered message", signature);
    assert(invalid_msg == false);

    std::cout << "PASSED\n";
}

void test_ed25519_public_only_verification() {
    std::cout << "-> Testing Ed25519 public-only key extraction and verification... " << std::flush;

    auto signer = ISignKey::Ed25519();
    std::string message = "Message to be verified by public key.";
    auto signature = signer->sign(message);

    // Extract public key PEM and reload as a public-only verifier
    std::string pub_pem = signer->public_pem();
    auto verifier = ISignKey::from_pem(pub_pem);

    assert(verifier->has_private_key() == false);

    // Signing with public-only key should throw
    bool threw = false;
    try {
        [[maybe_unused]] auto unused_sig = verifier->sign(message);
    } catch (const std::runtime_error&) {
        threw = true;
    }
    assert(threw);

    // Verifying with public-only key should succeed
    bool valid = verifier->verify(message, signature);
    assert(valid == true);

    std::cout << "PASSED\n";
}

void test_ed25519_pem_roundtrip() {
    std::cout << "-> Testing Ed25519 private and public PEM roundtrips... " << std::flush;

    auto original = ISignKey::Ed25519();
    std::string priv_pem = original->private_pem();
    std::string pub_pem = original->public_pem();

    // Recreate full keypair from private PEM
    auto restored_from_priv = ISignKey::from_pem(priv_pem);
    assert(restored_from_priv->has_private_key() == true);
    assert(restored_from_priv->public_pem() == pub_pem);

    std::string message = "PEM roundtrip payload.";
    auto signature = original->sign(message);

    // Restored private key should successfully sign and verify
    auto restored_sig = restored_from_priv->sign(message);
    assert(restored_from_priv->verify(message, signature) == true);

    std::cout << "PASSED\n";
}

void test_ed25519_invalid_inputs() {
    std::cout << "-> Testing Ed25519 invalid input handling and exceptions... " << std::flush;

    // Invalid PEM string should throw
    bool threw = false;
    try {
        auto bad_key = ISignKey::from_pem("-----BEGIN BAD KEY-----\ninvalid\n-----END BAD KEY-----");
    } catch (const std::exception&) {
        threw = true;
    }
    assert(threw);

    // Verification with invalid signature size should return false rather than throw
    auto signer = ISignKey::Ed25519();
    ISignKey::Data bad_sig(32, std::byte{0x00}); // Too short (expected 64 bytes)
    assert(signer->verify("test", bad_sig) == false);

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "    Running ISignKey (Ed25519) Test Suite    \n";

    test_ed25519_generation_and_signing();
    test_ed25519_public_only_verification();
    test_ed25519_pem_roundtrip();
    test_ed25519_invalid_inputs();

    std::cout << "   All Tests Passed Successfully!   \n";
    return 0;
}
