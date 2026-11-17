#include <gtest/gtest.h>
#include <cstddef>
#include <exception>
#include <stdexcept>
#include <string>
#include <string_view>

#include "Fixtures.hpp"
#include "crypto/ISignKey.hpp"

using crypto::ISignKey;

namespace {

// An Ed25519 signature's size, in bytes.
constexpr auto signature_size = 64U;

// RFC 8032's first Ed25519 test vector, its key in the PEM forms Python's
// cryptography package writes, as the Python node does: PKCS #8 for the
// private key, and SubjectPublicKeyInfo for the public key.
constexpr auto rfc_8032_private_pem =
    "-----BEGIN PRIVATE KEY-----\n"
    "MC4CAQAwBQYDK2VwBCIEIJ1hsZ3v/VpguoRK9JLsLMREScVpezJpGXA7rAMcrn9g\n"
    "-----END PRIVATE KEY-----\n";
constexpr auto rfc_8032_public_pem =
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEA11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo=\n"
    "-----END PUBLIC KEY-----\n";
// Its signature of the empty message.
constexpr auto rfc_8032_signature =
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555"
    "fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b";

// An X25519 public key: the same size as an Ed25519 one, for key agreement.
constexpr auto x25519_public_pem =
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VuAyEAj0DFrbaPJWJK5bIU6nZ6bslNgp09e14a0bpvPiE4KF8=\n"
    "-----END PUBLIC KEY-----\n";

// What ISignKey::from_pem() says is wrong with a PEM it turns down; empty if
// it reads it.
std::string refusal(std::string_view pem) {
    try {
        (void)ISignKey::from_pem(pem);
    } catch (const std::exception& error) {
        return error.what();
    }

    return {};
}

// Reads an RSA signing key from a PEM.
using ReadRsaKey = ISignKey::Ptr (*)(std::string_view pem);

// Each test runs on ISignKey::from_pem(), which reads RSA keys with Apple's
// RSA on macOS, and OpenSSL's elsewhere. On macOS, each runs on OpenSSL's as
// well.
class RsaSignKeyTest : public testing::TestWithParam<ReadRsaKey> {};

} // namespace

TEST(ISignKeyTest, Ed25519GenerationAndSigning) {
    const auto signer = ISignKey::Ed25519();
    const std::string message = "Libranet Ed25519 digital signature test message.";
    const auto signature = signer->sign(message);

    EXPECT_TRUE(signer->has_private_key());
    EXPECT_EQ(signer->id(), "ed25519-key");
    EXPECT_EQ(signature.size(), signature_size);
    EXPECT_TRUE(signer->verify(message, signature));
    EXPECT_FALSE(signer->verify("Tampered message", signature));
}

TEST(ISignKeyTest, Ed25519KeysDiffer) {
    EXPECT_NE(ISignKey::Ed25519()->public_pem(), ISignKey::Ed25519()->public_pem());
}

TEST(ISignKeyTest, Ed25519PublicOnlyVerification) {
    const auto signer = ISignKey::Ed25519();
    const std::string message = "Message to be verified by public key.";
    const auto signature = signer->sign(message);
    const auto verifier = ISignKey::from_pem(signer->public_pem());

    EXPECT_FALSE(verifier->has_private_key());
    EXPECT_THROW((void)verifier->sign(message), std::runtime_error);
    EXPECT_THROW((void)verifier->private_pem(), std::runtime_error);
    EXPECT_TRUE(verifier->verify(message, signature));
}

TEST(ISignKeyTest, Ed25519PemRoundtrip) {
    const auto original = ISignKey::Ed25519();
    const auto restored = ISignKey::from_pem(original->private_pem());
    const std::string message = "PEM roundtrip payload.";

    EXPECT_TRUE(restored->has_private_key());
    EXPECT_EQ(restored->public_pem(), original->public_pem());
    EXPECT_EQ(restored->private_pem(), original->private_pem());
    // Ed25519 signatures are deterministic: the same key signs the same
    // message the same way.
    EXPECT_EQ(restored->sign(message), original->sign(message));
    EXPECT_TRUE(restored->verify(message, original->sign(message)));
}

TEST(ISignKeyTest, Ed25519Rfc8032Vector) {
    const auto signer = ISignKey::from_pem(rfc_8032_private_pem);
    const auto verifier = ISignKey::from_pem(rfc_8032_public_pem);
    const auto signature = signer->sign("");

    EXPECT_EQ(fixtures::hex(signature), rfc_8032_signature);
    EXPECT_TRUE(verifier->verify("", signature));
    EXPECT_FALSE(verifier->verify("x", signature));
}

TEST(ISignKeyTest, Ed25519PemMatchesPython) {
    const auto signer = ISignKey::from_pem(rfc_8032_private_pem);
    const auto verifier = ISignKey::from_pem(rfc_8032_public_pem);

    EXPECT_EQ(signer->private_pem(), rfc_8032_private_pem);
    EXPECT_EQ(signer->public_pem(), rfc_8032_public_pem);
    EXPECT_EQ(verifier->public_pem(), rfc_8032_public_pem);
}

TEST(ISignKeyTest, Ed25519InvalidInputs) {
    // Invalid PEM string should throw
    EXPECT_THROW((void)ISignKey::from_pem(
                     "-----BEGIN BAD KEY-----\ninvalid\n-----END BAD KEY-----"),
                 std::exception);

    // A key of another type, or bytes that are no key, is refused.
    EXPECT_THROW((void)ISignKey::from_pem(x25519_public_pem),
                 std::invalid_argument);
    EXPECT_THROW((void)ISignKey::from_pem(
                     "-----BEGIN PUBLIC KEY-----\nYWJj\n-----END PUBLIC KEY-----\n"),
                 std::invalid_argument);

    // Verification with invalid signature size should return false rather than throw
    const auto signer = ISignKey::Ed25519();
    const ISignKey::Data bad_signature(signature_size / 2, std::byte{0x00});

    EXPECT_FALSE(signer->verify("test", bad_signature));
}

TEST(ISignKeyTest, DamagedRsaKeyRefusedByRsaReader) {
    std::string damaged = fixtures::rsa_pkcs1_private_pem;

    // The DER's first three bytes become zeros, so it is no SEQUENCE.
    damaged.replace(damaged.find('\n') + 1, 4, "AAAA");

    const auto message = refusal(damaged);

    // The RSA reader says what is wrong with the key; the Ed25519 reader,
    // given it, would say only that it does not know the label.
    EXPECT_FALSE(message.empty());
    EXPECT_EQ(message.find("label"), std::string::npos) << message;
}

TEST(ISignKeyTest, KeysOfOtherAlgorithmsRefused) {
    EXPECT_EQ(refusal(x25519_public_pem).find("Neither an RSA nor an Ed25519 key"), 0U);
    EXPECT_EQ(refusal("-----BEGIN CERTIFICATE-----\nYWJj\n-----END CERTIFICATE-----\n")
                  .find("Neither an RSA nor an Ed25519 key"),
              0U);
}

TEST_P(RsaSignKeyTest, ReadsPythonKey) {
    const auto signer = GetParam()(fixtures::rsa_pkcs1_private_pem);
    const auto verifier = GetParam()(fixtures::rsa_pkcs1_public_pem);
    const auto signature = fixtures::bytes(fixtures::rsa_pss_signature);

    EXPECT_EQ(signer->id(), "rsa-pss-sha256");
    EXPECT_TRUE(signer->has_private_key());
    EXPECT_FALSE(verifier->has_private_key());
    EXPECT_TRUE(verifier->verify(fixtures::rsa_message, signature));
    EXPECT_TRUE(verifier->verify(fixtures::rsa_message, signer->sign(fixtures::rsa_message)));
    EXPECT_FALSE(verifier->verify("Tampered message", signature));
    EXPECT_THROW((void)verifier->sign(fixtures::rsa_message), std::runtime_error);
    EXPECT_THROW((void)verifier->private_pem(), std::runtime_error);
    EXPECT_FALSE(verifier->public_pem().empty());
    EXPECT_FALSE(signer->private_pem().empty());
}

TEST_P(RsaSignKeyTest, ReadsPkcs8AndSpki) {
    const auto signer = GetParam()(fixtures::rsa_pkcs8_private_pem);
    const auto verifier = GetParam()(fixtures::rsa_spki_public_pem);
    const auto signature = fixtures::bytes(fixtures::rsa_pss_signature);

    EXPECT_TRUE(signer->has_private_key());
    EXPECT_FALSE(verifier->has_private_key());
    EXPECT_TRUE(verifier->verify(fixtures::rsa_message, signature));
    EXPECT_TRUE(verifier->verify(fixtures::rsa_message, signer->sign(fixtures::rsa_message)));
}

INSTANTIATE_TEST_SUITE_P(Factory, RsaSignKeyTest, testing::Values(&ISignKey::from_pem));
#if __APPLE__
#include "crypto/RsaSignKeyOpenSsl.hpp"

INSTANTIATE_TEST_SUITE_P(OpenSsl, RsaSignKeyTest,
                         testing::Values(&crypto::RsaSignKeyOpenSsl::from_pem));
#endif
