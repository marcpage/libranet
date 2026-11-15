#include <gtest/gtest.h>
#include <exception>
#include <string>

#include "crypto/IEncryptKey.hpp"
#include "crypto/IMultiKey.hpp"

using crypto::IEncryptKey;
using crypto::IMultiKey;

namespace {

constexpr auto rsa_key_bits = 2048;

} // namespace

TEST(IMultiKeyTest, RsaKeyGeneration) {
    const auto multi_key = IMultiKey::Rsa(rsa_key_bits);

    ASSERT_NE(multi_key, nullptr);
    EXPECT_EQ(multi_key->id(), "rsa");

    const auto signer = multi_key->RsaPssSha256();

    ASSERT_NE(signer, nullptr);
    EXPECT_TRUE(signer->has_private_key());
    EXPECT_NE(multi_key->RsaOaepSha256(), nullptr);
}

TEST(IMultiKeyTest, RsaSigningAndVerification) {
    const auto multi_key = IMultiKey::Rsa(rsa_key_bits);
    const auto signer = multi_key->RsaPssSha256();
    const std::string message = "Libranet Cryptographic Library Test Message";
    const auto signature = signer->sign(message);

    EXPECT_FALSE(signature.empty());
    EXPECT_TRUE(signer->verify(message, signature));
    EXPECT_FALSE(signer->verify("Tampered Message", signature));
}

TEST(IMultiKeyTest, RsaEncryptionAndDecryption) {
    const auto multi_key = IMultiKey::Rsa(rsa_key_bits);
    const auto encryptor = multi_key->RsaOaepSha256();
    const std::string plaintext = "Confidential Payload";
    // RSA-OAEP ignores initialization vectors, pass empty vector
    const IEncryptKey::InitializationVector iv;
    const auto ciphertext = encryptor->encrypt(plaintext, iv);

    EXPECT_FALSE(ciphertext.empty());
    EXPECT_EQ(encryptor->decrypt(ciphertext, iv), plaintext);
}

TEST(IMultiKeyTest, PemRoundtrip) {
    const auto original_key = IMultiKey::Rsa(rsa_key_bits);
    const std::string private_pem = original_key->private_pem();
    const std::string public_pem = original_key->public_pem();

    EXPECT_FALSE(private_pem.empty());
    EXPECT_FALSE(public_pem.empty());

    // Import from private PEM
    const auto imported_from_private = IMultiKey::from_pem(private_pem);

    ASSERT_NE(imported_from_private, nullptr);

    const auto imported_signer = imported_from_private->RsaPssSha256();
    const std::string message = "PEM Roundtrip Message";
    const auto signature = imported_signer->sign(message);

    EXPECT_TRUE(imported_signer->has_private_key());
    EXPECT_TRUE(imported_signer->verify(message, signature));

    // Import from public PEM
    const auto imported_from_public = IMultiKey::from_pem(public_pem);

    ASSERT_NE(imported_from_public, nullptr);

    const auto imported_verifier = imported_from_public->RsaPssSha256();

    EXPECT_FALSE(imported_verifier->has_private_key());
    EXPECT_TRUE(imported_verifier->verify(message, signature));
}

TEST(IMultiKeyTest, InvalidPem) {
    EXPECT_THROW(static_cast<void>(IMultiKey::from_pem(
                     "-----BEGIN GARBAGE-----\nYWJj\n-----END GARBAGE-----\n")),
                 std::exception);
}
