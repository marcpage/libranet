#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/types.h>
#include <openssl/x509.h>

#include "ISignKey.hpp"
#include "OpenSsl.hpp"
#include "Pem.hpp"
#include "types/Converter.hpp"

namespace crypto {

// An Ed25519 key, built on OpenSSL, in PEM as SubjectPublicKeyInfo and PKCS
// #8. A key of small order is refused (HTTP API §11.2).
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

    Ed25519OpenSsl(Ed25519OpenSsl&&) noexcept = default;
    Ed25519OpenSsl& operator=(Ed25519OpenSsl&&) noexcept = default;
    ~Ed25519OpenSsl() override = default;

    Ed25519OpenSsl(const Ed25519OpenSsl&) = delete;
    Ed25519OpenSsl& operator=(const Ed25519OpenSsl&) = delete;

private:
    using MessageContext = OpenSslObject<EVP_MD_CTX, EVP_MD_CTX_free>;

    // An Ed25519 signature's size, in bytes.
    static constexpr std::size_t signature_size = 64;
    // An Ed25519 public key's size, in bytes, in the raw form of RFC 8032
    // §5.1.2: the y-coordinate of its point, little-endian, with the sign of
    // its x-coordinate in the top bit of the last byte.
    static constexpr std::size_t raw_key_size = 32;
    // What clears the sign of x from the last byte of a raw key.
    static constexpr unsigned char y_mask = 0x7F;
    // The raw keys, in hex, of the eight Ed25519 points of small order: their
    // y-coordinates, with 0 and 1 also as p and p + 1, the encodings of them
    // that are not canonical. As in libsodium, a key is compared with the
    // sign of x cleared, so each matches its points whatever their sign.
    static constexpr std::array<std::string_view, 7> small_order_keys{
        "0000000000000000000000000000000000000000000000000000000000000000", // order 4
        "0100000000000000000000000000000000000000000000000000000000000000", // order 1
        "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05", // order 8
        "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a", // order 8
        "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f", // order 2
        "edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f", // order 4, as p
        "eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f", // order 1, as p + 1
    };

    Key m_key;
    bool m_has_private;

    [[nodiscard]] static Key _generate();
    [[nodiscard]] static bool _has_small_order(const EVP_PKEY* key);
};

inline ISignKey::Ptr Ed25519OpenSsl::create() {
    return std::make_unique<crypto::Ed25519OpenSsl>();
}

// cppcheck-suppress duplInheritedMember
inline ISignKey::Ptr Ed25519OpenSsl::from_pem(std::string_view pem) {
    auto [label, der_bytes] = Pem::unwrap(pem);

    if ("PRIVATE KEY" == label) {
        return std::make_unique<Ed25519OpenSsl>(OpenSslDer::read_private_key(der_bytes), true);
    }

    if ("PUBLIC KEY" == label) {
        return std::make_unique<Ed25519OpenSsl>(OpenSslDer::read_public_key(der_bytes), false);
    }

    throw std::invalid_argument("Unsupported or unrecognized PEM label: " + label);
}

inline Ed25519OpenSsl::Ed25519OpenSsl() : Ed25519OpenSsl(_generate(), true) {}

inline Ed25519OpenSsl::Ed25519OpenSsl(Key key, bool has_private)
    : m_key(std::move(key)), m_has_private(has_private) {
    if (!m_key || EVP_PKEY_is_a(m_key.get(), "ED25519") != 1) {
        throw std::invalid_argument("Not an Ed25519 key");
    }

    if (_has_small_order(m_key.get())) {
        throw std::invalid_argument("An Ed25519 key of small order, which anyone can sign for");
    }
}

inline Ed25519OpenSsl::Key Ed25519OpenSsl::_generate() {
    const OpenSslObject<EVP_PKEY_CTX, EVP_PKEY_CTX_free> context(
        EVP_PKEY_CTX_new_from_name(nullptr, "ED25519", nullptr));
    EVP_PKEY* key = nullptr;

    if (!context || EVP_PKEY_keygen_init(context.get()) != 1
        || EVP_PKEY_generate(context.get(), &key) != 1) {
        throw OpenSslError("Failed to generate an Ed25519 key");
    }

    return Key(key);
}

inline bool Ed25519OpenSsl::_has_small_order(const EVP_PKEY* key) {
    Data raw(raw_key_size);
    auto length = raw.size();

    if (EVP_PKEY_get_raw_public_key(key, OpenSslBytes::of(raw), &length) != 1
        || length != raw.size()) {
        throw OpenSslError("Failed to read an Ed25519 public key");
    }

    raw.back() &= std::byte{y_mask};

    const auto hex = types::Converter::hex_of(raw);

    return std::find(small_order_keys.begin(), small_order_keys.end(), hex)
           != small_order_keys.end();
}

inline ISignKey::Data Ed25519OpenSsl::sign(std::string_view data) const {
    if (!m_has_private) {
        throw std::runtime_error("Cannot sign: Ed25519 instance contains public key only");
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
    return 1 == result;
}

inline std::string Ed25519OpenSsl::public_pem() const {
    return Pem::wrap("PUBLIC KEY", OpenSslDer::write(m_key.get(), i2d_PUBKEY));
}

inline std::string Ed25519OpenSsl::private_pem() const {
    if (!m_has_private) {
        throw std::runtime_error("No private key available");
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
