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
class Ed25519 : public ISignKey {
public:
    using Key = OpenSslObject<EVP_PKEY, EVP_PKEY_free>;

    [[nodiscard]] static ISignKey::Ptr create();
    [[nodiscard]] static ISignKey::Ptr from_pem(std::string_view pem);

    [[nodiscard]] Data sign(std::string_view data) const override;
    [[nodiscard]] bool verify(std::string_view data, const Data& signature) const override;

    [[nodiscard]] std::string public_pem() const override;
    [[nodiscard]] std::string private_pem() const override;
    [[nodiscard]] std::string id() const override;
    [[nodiscard]] bool has_private_key() const noexcept override;

    Ed25519();
    Ed25519(Key key, bool has_private);
    Ed25519(Ed25519&&) noexcept = default;
    Ed25519& operator=(Ed25519&&) noexcept = default;
    ~Ed25519() override = default;

private:
    using MessageContext = OpenSslObject<EVP_MD_CTX, EVP_MD_CTX_free>;
    using DerEncoder = int (*)(const EVP_PKEY*, unsigned char**);

    // An Ed25519 signature's size, in bytes.
    static constexpr std::size_t signature_size = 64;

    Key m_key;
    bool m_has_private;

    Ed25519(const Ed25519&) = delete;
    Ed25519& operator=(const Ed25519&) = delete;

    [[nodiscard]] static Key generate();
    [[nodiscard]] static Key read_der(const Data& der, bool is_private);
    [[nodiscard]] Data der(DerEncoder encode) const;
};

inline ISignKey::Ptr Ed25519::create() {
    return std::make_unique<crypto::Ed25519>();
}

inline ISignKey::Ptr Ed25519::from_pem(std::string_view pem) {
    auto [label, der_bytes] = Pem::unwrap(pem);

    if (label == "PRIVATE KEY") {
        return std::make_unique<Ed25519>(read_der(der_bytes, true), true);
    }

    if (label == "PUBLIC KEY") {
        return std::make_unique<Ed25519>(read_der(der_bytes, false), false);
    }

    throw std::invalid_argument("Unsupported or unrecognized PEM label: " + label);
}

inline Ed25519::Ed25519() : Ed25519(generate(), true) {}

inline Ed25519::Ed25519(Key key, bool has_private)
    : m_key(std::move(key)), m_has_private(has_private) {
    if (!m_key || EVP_PKEY_is_a(m_key.get(), "ED25519") != 1) {
        throw std::invalid_argument("Not an Ed25519 key.");
    }
}

inline Ed25519::Key Ed25519::generate() {
    const OpenSslObject<EVP_PKEY_CTX, EVP_PKEY_CTX_free> context(
        EVP_PKEY_CTX_new_from_name(nullptr, "ED25519", nullptr));
    EVP_PKEY* key = nullptr;

    if (!context || EVP_PKEY_keygen_init(context.get()) != 1
        || EVP_PKEY_generate(context.get(), &key) != 1) {
        throw OpenSslError("Failed to generate an Ed25519 key");
    }

    return Key(key);
}

// A key from its DER encoding: PKCS #8 for a private key, and
// SubjectPublicKeyInfo for a public key. Bytes after the key are refused.
inline Ed25519::Key Ed25519::read_der(const Data& der, bool is_private) {
    const auto* const start = OpenSslBytes::of(der);
    const auto* next = start;
    const auto length = static_cast<long>(der.size());
    Key key(is_private ? d2i_AutoPrivateKey(nullptr, &next, length)
                       : d2i_PUBKEY(nullptr, &next, length));

    if (!key) {
        ERR_clear_error();
        throw std::invalid_argument(is_private ? "Not a PKCS #8 private key."
                                               : "Not a SubjectPublicKeyInfo public key.");
    }

    if (static_cast<std::size_t>(next - start) != der.size()) {
        throw std::invalid_argument("Bytes after the key in its PEM.");
    }

    return key;
}

inline ISignKey::Data Ed25519::der(DerEncoder encode) const {
    const auto length = encode(m_key.get(), nullptr);

    if (length <= 0) {
        throw OpenSslError("Failed to encode an Ed25519 key");
    }

    Data bytes(static_cast<std::size_t>(length));
    auto* next = OpenSslBytes::of(bytes);

    if (encode(m_key.get(), &next) != length) {
        throw OpenSslError("Failed to encode an Ed25519 key");
    }

    return bytes;
}

inline ISignKey::Data Ed25519::sign(std::string_view data) const {
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

inline bool Ed25519::verify(std::string_view data, const Data& signature) const {
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

inline std::string Ed25519::public_pem() const {
    return Pem::wrap("PUBLIC KEY", der(i2d_PUBKEY));
}

inline std::string Ed25519::private_pem() const {
    if (!m_has_private) {
        throw std::runtime_error("No private key available.");
    }

    // A key with no older form of its own, as Ed25519 has none, is written
    // in PKCS #8.
    return Pem::wrap("PRIVATE KEY", der(i2d_PrivateKey));
}

inline std::string Ed25519::id() const {
    return "ed25519-key";
}

inline bool Ed25519::has_private_key() const noexcept {
    return m_has_private;
}

} // namespace crypto
