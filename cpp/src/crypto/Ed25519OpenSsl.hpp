#pragma once

#include <cstddef>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/x509.h>

#include "ISignKey.hpp"
#include "OpenSsl.hpp"
#include "Pem.hpp"

namespace crypto {

// An Ed25519 key, built on OpenSSL. Its PEM forms are the standard ones,
// which the Python node reads and writes too: SubjectPublicKeyInfo for the
// public key, and PKCS #8 for the private key.
class Ed25519OpenSsl : public ISignKey {
public:
    using Key = OpenSslKey;

    [[nodiscard]] static ISignKey::Ptr create();
    [[nodiscard]] static ISignKey::Ptr from_pem(std::string_view pem);

    [[nodiscard]] Data sign(std::string_view data) const override;
    [[nodiscard]] bool verify(std::string_view data, const Data& signature) const override;

    [[nodiscard]] std::string public_pem() const override;
    [[nodiscard]] std::string private_pem() const override;
    [[nodiscard]] std::string id() const override;
    [[nodiscard]] bool has_private_key() const noexcept override;

    Ed25519OpenSsl();
    Ed25519OpenSsl(Key key, bool has_private);
    Ed25519OpenSsl(const Ed25519OpenSsl&) = delete;
    Ed25519OpenSsl& operator=(const Ed25519OpenSsl&) = delete;
    Ed25519OpenSsl(Ed25519OpenSsl&&) noexcept = default;
    Ed25519OpenSsl& operator=(Ed25519OpenSsl&&) noexcept = default;
    ~Ed25519OpenSsl() override = default;

private:
    using MessageContext = OpenSslObject<EVP_MD_CTX, EVP_MD_CTX_free>;

    // An Ed25519 signature's size, in bytes.
    static constexpr std::size_t signature_size = 64;

    Key m_key;
    bool m_has_private;

    [[nodiscard]] static Key generate();
};

inline ISignKey::Ptr Ed25519OpenSsl::create() {
    return std::make_unique<crypto::Ed25519OpenSsl>();
}

// cppcheck-suppress duplInheritedMember
inline ISignKey::Ptr Ed25519OpenSsl::from_pem(std::string_view pem) {
    auto [label, der_bytes] = Pem::unwrap(pem);

    if (label == "PRIVATE KEY") {
        return std::make_unique<Ed25519OpenSsl>(OpenSslDer::read_private_key(der_bytes), true);
    }

    if (label == "PUBLIC KEY") {
        return std::make_unique<Ed25519OpenSsl>(OpenSslDer::read_public_key(der_bytes), false);
    }

    throw std::invalid_argument("Unsupported or unrecognized PEM label: " + label);
}

inline Ed25519OpenSsl::Ed25519OpenSsl() : Ed25519OpenSsl(generate(), true) {}

inline Ed25519OpenSsl::Ed25519OpenSsl(Key key, bool has_private)
    : m_key(std::move(key)), m_has_private(has_private) {
    if (!m_key || EVP_PKEY_is_a(m_key.get(), "ED25519") != 1) {
        throw std::invalid_argument("Not an Ed25519 key.");
    }
}

inline Ed25519OpenSsl::Key Ed25519OpenSsl::generate() {
    const OpenSslObject<EVP_PKEY_CTX, EVP_PKEY_CTX_free> context(
        EVP_PKEY_CTX_new_from_name(nullptr, "ED25519", nullptr));
    EVP_PKEY* key = nullptr;

    if (!context || EVP_PKEY_keygen_init(context.get()) != 1
        || EVP_PKEY_generate(context.get(), &key) != 1) {
        throw OpenSslError("Failed to generate an Ed25519 key");
    }

    return Key(key);
}

inline ISignKey::Data Ed25519OpenSsl::sign(std::string_view data) const {
    if (!m_has_private) {
        throw std::runtime_error("Cannot sign: Ed25519 instance contains public key only.");
    }

    const MessageContext context(EVP_MD_CTX_new());
    Data signature(signature_size);
    auto length = signature.size();

    if (!context
        || EVP_DigestSignInit(context.get(), nullptr, nullptr, nullptr, m_key.get()) != 1
        || EVP_DigestSign(context.get(), OpenSslBytes::of(signature), &length,
                          OpenSslBytes::of(data), data.size())
               != 1) {
        throw OpenSslError("Failed to sign with an Ed25519 key");
    }

    signature.resize(length);
    return signature;
}

inline bool Ed25519OpenSsl::verify(std::string_view data, const Data& signature) const {
    if (signature.size() != signature_size) {
        return false;
    }

    const MessageContext context(EVP_MD_CTX_new());

    if (!context
        || EVP_DigestVerifyInit(context.get(), nullptr, nullptr, nullptr, m_key.get()) != 1) {
        throw OpenSslError("Failed to start verifying with an Ed25519 key");
    }

    const auto result = EVP_DigestVerify(context.get(), OpenSslBytes::of(signature),
                                         signature.size(), OpenSslBytes::of(data), data.size());

    // A signature that does not match leaves an error in OpenSSL's queue.
    ERR_clear_error();
    return result == 1;
}

inline std::string Ed25519OpenSsl::public_pem() const {
    return Pem::wrap("PUBLIC KEY", OpenSslDer::write(m_key.get(), i2d_PUBKEY));
}

inline std::string Ed25519OpenSsl::private_pem() const {
    if (!m_has_private) {
        throw std::runtime_error("No private key available.");
    }

    // A key with no older form of its own, as Ed25519 has none, is written
    // in PKCS #8.
    return Pem::wrap("PRIVATE KEY", OpenSslDer::write(m_key.get(), i2d_PrivateKey));
}

inline std::string Ed25519OpenSsl::id() const {
    return "ed25519-key";
}

inline bool Ed25519OpenSsl::has_private_key() const noexcept {
    return m_has_private;
}

} // namespace crypto
