#pragma once

#include <vector>
#include <string>
#include <string_view>
#include <stdexcept>
#include <memory>
#include <cstring>

#include "ISignKey.hpp"
#include "Pem.hpp"

extern "C" {
#include "monocypher.h"
}

namespace crypto {

class Ed25519 : public ISignKey {
public:
    [[nodiscard]] static ISignKey::Ptr create();
    [[nodiscard]] static ISignKey::Ptr from_pem(std::string_view pem);

    [[nodiscard]] Data sign(std::string_view data) const override;
    [[nodiscard]] bool verify(std::string_view data, const Data& signature) const override;

    [[nodiscard]] std::string public_pem() const override;
    [[nodiscard]] std::string private_pem() const override;
    [[nodiscard]] std::string id() const override;
    [[nodiscard]] bool has_private_key() const noexcept override;

    Ed25519();
    Ed25519(const Data& secret_key_bytes, const Data& public_key);
    explicit Ed25519(const Data& public_key);
    Ed25519(Ed25519&&) noexcept = default;
    Ed25519& operator=(Ed25519&&) noexcept = default;
    ~Ed25519() override;

private:
    uint8_t m_secret_key[64]{0}; // Monocypher EdDSA secret key holds 64 bytes (seed + public key bundled)
    uint8_t m_public_key[32];
    bool m_has_private;

    Ed25519(const Ed25519&) = delete;
    Ed25519& operator=(const Ed25519&) = delete;

};

inline ISignKey::Ptr Ed25519::create() {
    return std::make_unique<crypto::Ed25519>();
}

inline ISignKey::Ptr Ed25519::from_pem(std::string_view pem) {
    auto [label, der_bytes] = Pem::unwrap(pem);

    if (label == "PRIVATE KEY" || label == "Ed25519 PRIVATE KEY") {
        Data secret_bytes;
        uint8_t pub[32];
        uint8_t sec[64];

        if (der_bytes.size() == 32) {
            uint8_t seed[32];
            std::memcpy(seed, der_bytes.data(), 32);
            crypto_eddsa_key_pair(sec, pub, seed);
            secret_bytes = Data(64);
            std::memcpy(secret_bytes.data(), sec, 64);
        } else if (der_bytes.size() == 64) {
            secret_bytes = der_bytes;
            std::memcpy(sec, secret_bytes.data(), 64);
            std::memcpy(pub, sec + 32, 32);
        } else if (der_bytes.size() > 64) {
            secret_bytes = Data(der_bytes.end() - 64, der_bytes.end());
            std::memcpy(sec, secret_bytes.data(), 64);
            std::memcpy(pub, sec + 32, 32);
        } else {
            throw std::invalid_argument("Invalid Ed25519 private key DER byte length.");
        }

        Data pub_data(32);

        std::memcpy(pub_data.data(), pub, 32);
        return std::make_unique<Ed25519>(secret_bytes, pub_data);
    }
    else if (label == "PUBLIC KEY" || label == "Ed25519 PUBLIC KEY") {
        Data pub_key;

        if (der_bytes.size() == 32) {
            pub_key = der_bytes;
        } else if (der_bytes.size() == 44) { // PKIX format prefix offset
            pub_key = Data(der_bytes.end() - 32, der_bytes.end());
        } else {
            throw std::invalid_argument("Invalid Ed25519 public key DER byte length.");
        }

        return std::make_unique<Ed25519>(pub_key);
    }

    throw std::invalid_argument("Unsupported or unrecognized PEM label: " + label);
}

inline Ed25519::Ed25519() : m_has_private(true) {
    uint8_t seed[32];
    arc4random_buf(seed, sizeof(seed));
    crypto_eddsa_key_pair(m_secret_key, m_public_key, seed);
    crypto_wipe(seed, sizeof(seed));
}

inline Ed25519::Ed25519(const Data& secret_key_bytes, const Data& public_key) : m_has_private(true) {
    if (secret_key_bytes.size() != 64 || public_key.size() != 32) {
        throw std::invalid_argument("Invalid Ed25519 key component sizes.");
    }

    std::memcpy(m_secret_key, secret_key_bytes.data(), 64);
    std::memcpy(m_public_key, public_key.data(), 32);
}

inline Ed25519::Ed25519(const Data& public_key) : m_has_private(false) {
    if (public_key.size() != 32) {
        throw std::invalid_argument("Ed25519 public key must be exactly 32 bytes.");
    }

    std::memcpy(m_public_key, public_key.data(), 32);
}

inline Ed25519::~Ed25519() {
    if (m_has_private) {
        crypto_wipe(m_secret_key, sizeof(m_secret_key));
    }
}

inline ISignKey::Data Ed25519::sign(std::string_view data) const {
    if (!m_has_private) {
        throw std::runtime_error("Cannot sign: Ed25519 instance contains public key only.");
    }

    Data signature(64);

    crypto_eddsa_sign(
        reinterpret_cast<uint8_t*>(signature.data()),
        m_secret_key,
        reinterpret_cast<const uint8_t*>(data.data()),
        data.size()
    );

    return signature;
}

inline bool Ed25519::verify(std::string_view data, const Data& signature) const {
    if (signature.size() != 64) {
        return false;
    }

    int result = crypto_eddsa_check(
        reinterpret_cast<const uint8_t*>(signature.data()),
        m_public_key,
        reinterpret_cast<const uint8_t*>(data.data()),
        data.size()
    );

    return (result == 0);
}

inline std::string Ed25519::public_pem() const {
    std::vector<std::byte> bytes(32);

    std::memcpy(bytes.data(), m_public_key, 32);
    return Pem::wrap("PUBLIC KEY", bytes);
}

inline std::string Ed25519::private_pem() const {
    if (!m_has_private) {
        throw std::runtime_error("No private key available.");
    }

    std::vector<std::byte> bytes(64);

    std::memcpy(bytes.data(), m_secret_key, 64);
    return Pem::wrap("PRIVATE KEY", bytes);
}

inline std::string Ed25519::id() const {
    return "ed25519-key";
}

inline bool Ed25519::has_private_key() const noexcept {
    return m_has_private;
}

} // namespace crypto
