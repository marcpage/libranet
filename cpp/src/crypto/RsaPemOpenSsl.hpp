#pragma once

#include <cstddef>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include <openssl/evp.h>
#include <openssl/x509.h>

#include "OpenSsl.hpp"
#include "Pem.hpp"

namespace crypto {

// RSA keys to and from PEM, with OpenSSL. A public key is written as
// SubjectPublicKeyInfo and a private key as PKCS #8, the forms the Python
// node writes. Either is read in those forms, or in PKCS #1, labeled
// "RSA PUBLIC KEY" or "RSA PRIVATE KEY".
class RsaPemOpenSsl {
public:
    // A key read from a PEM, and whether it holds the private key.
    struct KeyPair {
        OpenSslKey key;
        bool has_private = false;
    };

    [[nodiscard]] static std::string public_pem(const EVP_PKEY* key);
    [[nodiscard]] static std::string private_pem(const EVP_PKEY* key);
    [[nodiscard]] static KeyPair from_pem(std::string_view pem);

    RsaPemOpenSsl() = delete;
    ~RsaPemOpenSsl() = delete;
    RsaPemOpenSsl(const RsaPemOpenSsl&) = delete;
    RsaPemOpenSsl& operator=(const RsaPemOpenSsl&) = delete;
    RsaPemOpenSsl(RsaPemOpenSsl&&) = delete;
    RsaPemOpenSsl& operator=(RsaPemOpenSsl&&) = delete;

private:
    [[nodiscard]] static bool is_private_label(std::string_view label);
    [[nodiscard]] static OpenSslKey read_der(const std::vector<std::byte>& der,
                                             std::string_view label);
};

inline std::string RsaPemOpenSsl::public_pem(const EVP_PKEY* key) {
    return Pem::wrap("PUBLIC KEY", OpenSslDer::write(key, i2d_PUBKEY));
}

inline std::string RsaPemOpenSsl::private_pem(const EVP_PKEY* key) {
    // i2d_PrivateKey() would write an RSA key in PKCS #1, its own older
    // form, so the key is put in PKCS #8 first.
    const OpenSslObject<PKCS8_PRIV_KEY_INFO, PKCS8_PRIV_KEY_INFO_free> info(
        EVP_PKEY2PKCS8(key));

    if (!info) {
        throw OpenSslError("Failed to put an RSA private key in PKCS #8");
    }

    return Pem::wrap("PRIVATE KEY", OpenSslDer::write(info.get(), i2d_PKCS8_PRIV_KEY_INFO));
}

inline RsaPemOpenSsl::KeyPair RsaPemOpenSsl::from_pem(std::string_view pem) {
    auto [label, der_bytes] = Pem::unwrap(pem);
    KeyPair pair{read_der(der_bytes, label), is_private_label(label)};

    if (EVP_PKEY_is_a(pair.key.get(), "RSA") != 1) {
        throw std::invalid_argument("Not an RSA key");
    }

    return pair;
}

inline bool RsaPemOpenSsl::is_private_label(std::string_view label) {
    return "PRIVATE KEY" == label || "RSA PRIVATE KEY" == label;
}

inline OpenSslKey RsaPemOpenSsl::read_der(const std::vector<std::byte>& der,
                                          std::string_view label) {
    if (is_private_label(label)) {
        return OpenSslDer::read_private_key(der);
    }

    if ("PUBLIC KEY" == label) {
        return OpenSslDer::read_public_key(der);
    }

    if ("RSA PUBLIC KEY" == label) {
        return OpenSslDer::read_rsa_public_key(der);
    }

    throw std::invalid_argument("Unsupported or unrecognized PEM label: " + std::string(label));
}

} // namespace crypto
