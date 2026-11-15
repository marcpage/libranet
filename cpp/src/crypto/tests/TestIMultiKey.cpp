#include <gtest/gtest.h>
#include <cstddef>
#include <exception>
#include <stdexcept>
#include <string>
#include <string_view>

#include "Fixtures.hpp"
#include "crypto/IEncryptKey.hpp"
#include "crypto/IMultiKey.hpp"
#include "crypto/RsaOpenSsl.hpp"
#if __APPLE__
#include "crypto/RsaMacos.hpp"
#endif

using crypto::IEncryptKey;
using crypto::IMultiKey;
using crypto::RsaOpenSsl;

namespace {

constexpr auto rsa_key_bits = 2048;

// The RSA a test runs on: how it makes a key, and how it reads one.
struct Rsa {
    IMultiKey::Ptr (*generate)(std::size_t key_bits);
    IMultiKey::Ptr (*from_pem)(std::string_view pem);
};

// Each test runs on what IMultiKey makes: Apple's on macOS, and OpenSSL's
// elsewhere. On macOS, each runs on OpenSSL's as well.
class IMultiKeyTest : public testing::TestWithParam<Rsa> {};

} // namespace

TEST_P(IMultiKeyTest, RsaKeyGeneration) {
    const auto multi_key = GetParam().generate(rsa_key_bits);

    ASSERT_NE(multi_key, nullptr);
    EXPECT_EQ(multi_key->id(), "rsa");

    const auto signer = multi_key->RsaPssSha256();

    ASSERT_NE(signer, nullptr);
    EXPECT_TRUE(signer->has_private_key());
    EXPECT_EQ(signer->id(), "rsa-pss-sha256");
    EXPECT_NE(multi_key->RsaOaepSha256(), nullptr);
}

TEST_P(IMultiKeyTest, RsaSigningAndVerification) {
    const auto multi_key = GetParam().generate(rsa_key_bits);
    const auto signer = multi_key->RsaPssSha256();
    const std::string message = "Libranet Cryptographic Library Test Message";
    const auto signature = signer->sign(message);

    EXPECT_FALSE(signature.empty());
    EXPECT_TRUE(signer->verify(message, signature));
    EXPECT_FALSE(signer->verify("Tampered Message", signature));
}

TEST_P(IMultiKeyTest, RsaEncryptionAndDecryption) {
    const auto multi_key = GetParam().generate(rsa_key_bits);
    const auto encryptor = multi_key->RsaOaepSha256();
    const std::string plaintext = "Confidential Payload";
    // RSA-OAEP ignores initialization vectors, pass empty vector
    const IEncryptKey::InitializationVector initialization_vector;
    const auto ciphertext = encryptor->encrypt(plaintext, initialization_vector);

    EXPECT_FALSE(ciphertext.empty());
    EXPECT_EQ(encryptor->decrypt(ciphertext, initialization_vector), plaintext);
}

TEST_P(IMultiKeyTest, PemRoundtrip) {
    const auto original_key = GetParam().generate(rsa_key_bits);
    const std::string private_pem = original_key->private_pem();
    const std::string public_pem = original_key->public_pem();

    EXPECT_FALSE(private_pem.empty());
    EXPECT_FALSE(public_pem.empty());

    // Import from private PEM
    const auto imported_from_private = GetParam().from_pem(private_pem);

    ASSERT_NE(imported_from_private, nullptr);

    const auto imported_signer = imported_from_private->RsaPssSha256();
    const std::string message = "PEM Roundtrip Message";
    const auto signature = imported_signer->sign(message);

    EXPECT_TRUE(imported_signer->has_private_key());
    EXPECT_TRUE(imported_signer->verify(message, signature));

    // Import from public PEM
    const auto imported_from_public = GetParam().from_pem(public_pem);

    ASSERT_NE(imported_from_public, nullptr);

    const auto imported_verifier = imported_from_public->RsaPssSha256();

    EXPECT_FALSE(imported_verifier->has_private_key());
    EXPECT_TRUE(imported_verifier->verify(message, signature));
}

TEST_P(IMultiKeyTest, PublicKeyOnly) {
    const auto key = GetParam().from_pem(fixtures::rsa_pkcs1_public_pem);
    const auto ciphertext = fixtures::bytes(fixtures::rsa_oaep_ciphertext);

    EXPECT_THROW(static_cast<void>(key->private_pem()), std::runtime_error);
    EXPECT_THROW(static_cast<void>(key->RsaPssSha256()->sign(fixtures::rsa_message)),
                 std::runtime_error);
    EXPECT_THROW(static_cast<void>(key->RsaOaepSha256()->decrypt(ciphertext, {})),
                 std::runtime_error);
    EXPECT_FALSE(key->RsaOaepSha256()->encrypt(fixtures::rsa_oaep_plaintext, {}).empty());
}

TEST_P(IMultiKeyTest, ReadsPythonKeyInPkcs1) {
    const auto private_key = GetParam().from_pem(fixtures::rsa_pkcs1_private_pem);
    const auto public_key = GetParam().from_pem(fixtures::rsa_pkcs1_public_pem);
    const auto signature = fixtures::bytes(fixtures::rsa_pss_signature);
    const auto ciphertext = fixtures::bytes(fixtures::rsa_oaep_ciphertext);

    EXPECT_TRUE(private_key->RsaPssSha256()->verify(fixtures::rsa_message, signature));
    EXPECT_TRUE(public_key->RsaPssSha256()->verify(fixtures::rsa_message, signature));
    EXPECT_FALSE(public_key->RsaPssSha256()->verify("Tampered Message", signature));
    EXPECT_EQ(private_key->RsaOaepSha256()->decrypt(ciphertext, {}),
              fixtures::rsa_oaep_plaintext);
}

TEST_P(IMultiKeyTest, InvalidPem) {
    EXPECT_THROW(static_cast<void>(GetParam().from_pem(
                     "-----BEGIN GARBAGE-----\nYWJj\n-----END GARBAGE-----\n")),
                 std::exception);
}

// Each RSA writes the forms the Python node writes, byte for byte, and reads
// them back.
TEST_P(IMultiKeyTest, ReadsAndWritesPythonForms) {
    const auto from_pkcs1 = GetParam().from_pem(fixtures::rsa_pkcs1_private_pem);
    const auto from_pkcs8 = GetParam().from_pem(fixtures::rsa_pkcs8_private_pem);
    const auto from_spki = GetParam().from_pem(fixtures::rsa_spki_public_pem);
    const auto signature = fixtures::bytes(fixtures::rsa_pss_signature);
    const auto ciphertext = fixtures::bytes(fixtures::rsa_oaep_ciphertext);

    EXPECT_EQ(from_pkcs1->private_pem(), fixtures::rsa_pkcs8_private_pem);
    EXPECT_EQ(from_pkcs1->public_pem(), fixtures::rsa_spki_public_pem);
    EXPECT_EQ(from_pkcs8->private_pem(), fixtures::rsa_pkcs8_private_pem);
    EXPECT_EQ(from_pkcs8->public_pem(), fixtures::rsa_spki_public_pem);
    EXPECT_EQ(from_spki->public_pem(), fixtures::rsa_spki_public_pem);
    EXPECT_TRUE(from_spki->RsaPssSha256()->verify(fixtures::rsa_message, signature));
    EXPECT_EQ(from_pkcs8->RsaOaepSha256()->decrypt(ciphertext, {}), fixtures::rsa_oaep_plaintext);
}

TEST_P(IMultiKeyTest, RefusesOtherKeys) {
    // An Ed25519 key, from RFC 8032.
    EXPECT_THROW(static_cast<void>(GetParam().from_pem(
                     "-----BEGIN PUBLIC KEY-----\n"
                     "MCowBQYDK2VwAyEA11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo=\n"
                     "-----END PUBLIC KEY-----\n")),
                 std::invalid_argument);
    // A public key in PKCS #1 labeled as one in SubjectPublicKeyInfo.
    EXPECT_THROW(static_cast<void>(GetParam().from_pem(
                     "-----BEGIN PUBLIC KEY-----\n"
                     "MIGJAoGBALv4749Q4HM+vxN+Qv1XxGfFV+VfX8Igz1rRV997Hm68DNTXYjvLbxq4\n"
                     "2z8RCov92ZBo0YCGkDTeMXwvy1X23NWoNGO5PDv7OyPJ9SzkeyA+zBzurkm9y5aJ\n"
                     "h52r3os5R7ekQr3Kgcm4GyqOUWR01hGXWJMjGK6Cebju/MfbD2ihAgMBAAE=\n"
                     "-----END PUBLIC KEY-----\n")),
                 std::invalid_argument);
}

INSTANTIATE_TEST_SUITE_P(Factory, IMultiKeyTest,
                         testing::Values(Rsa{&IMultiKey::Rsa, &IMultiKey::from_pem}));
#if __APPLE__
INSTANTIATE_TEST_SUITE_P(OpenSsl, IMultiKeyTest,
                         testing::Values(Rsa{&RsaOpenSsl::for_bit_size, &RsaOpenSsl::from_pem}));
#endif

TEST(RsaOpenSslTest, RefusesKeySizeOutOfRange) {
    EXPECT_THROW(static_cast<void>(RsaOpenSsl::for_bit_size(std::size_t{1} << 40U)),
                 std::invalid_argument);
}

#if __APPLE__
// What Apple's RSA signs or encrypts, OpenSSL's verifies or decrypts, and
// the other way around.
TEST(RsaInteropTest, AppleAndOpenSslAgree) {
    const auto apple = crypto::RsaMacos::from_pem(fixtures::rsa_pkcs1_private_pem);
    const auto open_ssl = RsaOpenSsl::from_pem(fixtures::rsa_pkcs1_private_pem);
    const std::string message = fixtures::rsa_message;
    const std::string plaintext = fixtures::rsa_oaep_plaintext;

    EXPECT_TRUE(open_ssl->RsaPssSha256()->verify(message, apple->RsaPssSha256()->sign(message)));
    EXPECT_TRUE(apple->RsaPssSha256()->verify(message, open_ssl->RsaPssSha256()->sign(message)));
    EXPECT_EQ(open_ssl->RsaOaepSha256()->decrypt(apple->RsaOaepSha256()->encrypt(plaintext, {}), {}),
              plaintext);
    EXPECT_EQ(apple->RsaOaepSha256()->decrypt(open_ssl->RsaOaepSha256()->encrypt(plaintext, {}), {}),
              plaintext);
}

// What each writes in PEM, the other reads as the same key.
TEST(RsaInteropTest, AppleAndOpenSslReadEachOthersPem) {
    const auto apple = crypto::RsaMacos::for_bit_size(rsa_key_bits);
    const auto open_ssl = RsaOpenSsl::for_bit_size(rsa_key_bits);
    const auto apple_from_open_ssl = crypto::RsaMacos::from_pem(open_ssl->private_pem());
    const auto open_ssl_from_apple = RsaOpenSsl::from_pem(apple->private_pem());
    const std::string message = fixtures::rsa_message;

    EXPECT_EQ(apple_from_open_ssl->private_pem(), open_ssl->private_pem());
    EXPECT_EQ(apple_from_open_ssl->public_pem(), open_ssl->public_pem());
    EXPECT_EQ(open_ssl_from_apple->private_pem(), apple->private_pem());
    EXPECT_EQ(open_ssl_from_apple->public_pem(), apple->public_pem());
    EXPECT_TRUE(RsaOpenSsl::from_pem(apple->public_pem())
                    ->RsaPssSha256()
                    ->verify(message, apple->RsaPssSha256()->sign(message)));
    EXPECT_TRUE(crypto::RsaMacos::from_pem(open_ssl->public_pem())
                    ->RsaPssSha256()
                    ->verify(message, open_ssl->RsaPssSha256()->sign(message)));
}
#endif
