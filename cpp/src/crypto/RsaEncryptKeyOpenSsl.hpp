#pragma once

#include <cstddef>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include <openssl/evp.h>
#include <openssl/rsa.h>

#include "IEncryptKey.hpp"
#include "OpenSsl.hpp"

namespace crypto {

// RSAES-OAEP with SHA-256, and MGF1 over it, built on OpenSSL, as Apple's
// Security framework and the Python node encrypt with. It takes no
// initialization vector, and ignores any it is given.
class RsaEncryptKeyOpenSsl : public IEncryptKey {
public:
    RsaEncryptKeyOpenSsl(OpenSslKey key, bool has_private);
    ~RsaEncryptKeyOpenSsl() override = default;

    RsaEncryptKeyOpenSsl(const RsaEncryptKeyOpenSsl&) = delete;
    RsaEncryptKeyOpenSsl& operator=(const RsaEncryptKeyOpenSsl&) = delete;
    RsaEncryptKeyOpenSsl(RsaEncryptKeyOpenSsl&&) noexcept = default;
    RsaEncryptKeyOpenSsl& operator=(RsaEncryptKeyOpenSsl&&) noexcept = default;

    [[nodiscard]] Data encrypt(std::string_view plaintext,
                               const InitializationVector& vector) const override;
    [[nodiscard]] std::string decrypt(const Data& ciphertext,
                                      const InitializationVector& vector) const override;

private:
    using KeyContext = OpenSslObject<EVP_PKEY_CTX, EVP_PKEY_CTX_free>;

    OpenSslKey m_key;
    bool m_has_private;

    [[nodiscard]] KeyContext _make_context() const;
    static void _use_oaep(EVP_PKEY_CTX* context);
};

inline RsaEncryptKeyOpenSsl::RsaEncryptKeyOpenSsl(OpenSslKey key, bool has_private)
    : m_key(std::move(key)), m_has_private(has_private) {}

inline IEncryptKey::Data RsaEncryptKeyOpenSsl::encrypt(
    std::string_view plaintext, [[maybe_unused]] const InitializationVector& vector) const {
    const auto context = _make_context();

    if (EVP_PKEY_encrypt_init(context.get()) <= 0) {
        throw OpenSslError("Failed to start RSA OAEP encryption");
    }

    _use_oaep(context.get());

    std::size_t length = 0;

    if (EVP_PKEY_encrypt(context.get(), nullptr, &length, OpenSslBytes::of(plaintext),
                         plaintext.size())
        <= 0) {
        throw OpenSslError("RSA OAEP encryption failed");
    }

    Data ciphertext(length);

    if (EVP_PKEY_encrypt(context.get(), OpenSslBytes::of(ciphertext), &length,
                         OpenSslBytes::of(plaintext), plaintext.size())
        <= 0) {
        throw OpenSslError("RSA OAEP encryption failed");
    }

    ciphertext.resize(length);
    return ciphertext;
}

inline std::string RsaEncryptKeyOpenSsl::decrypt(
    const Data& ciphertext, [[maybe_unused]] const InitializationVector& vector) const {
    if (!m_has_private) {
        throw std::runtime_error("RSA private key required for decryption");
    }

    const auto context = _make_context();

    if (EVP_PKEY_decrypt_init(context.get()) <= 0) {
        throw OpenSslError("Failed to start RSA OAEP decryption");
    }

    _use_oaep(context.get());

    std::size_t length = 0;

    if (EVP_PKEY_decrypt(context.get(), nullptr, &length, OpenSslBytes::of(ciphertext),
                         ciphertext.size())
        <= 0) {
        throw OpenSslError("RSA OAEP decryption failed");
    }

    std::string plaintext(length, '\0');

    if (EVP_PKEY_decrypt(context.get(), OpenSslBytes::of(plaintext), &length,
                         OpenSslBytes::of(ciphertext), ciphertext.size())
        <= 0) {
        throw OpenSslError("RSA OAEP decryption failed");
    }

    plaintext.resize(length);
    return plaintext;
}

inline RsaEncryptKeyOpenSsl::KeyContext RsaEncryptKeyOpenSsl::_make_context() const {
    KeyContext context(EVP_PKEY_CTX_new_from_pkey(nullptr, m_key.get(), nullptr));

    if (!context) {
        throw OpenSslError("Failed to start RSA OAEP");
    }

    return context;
}

inline void RsaEncryptKeyOpenSsl::_use_oaep(EVP_PKEY_CTX* context) {
    if (EVP_PKEY_CTX_set_rsa_padding(context, RSA_PKCS1_OAEP_PADDING) <= 0
        || EVP_PKEY_CTX_set_rsa_oaep_md(context, EVP_sha256()) <= 0
        || EVP_PKEY_CTX_set_rsa_mgf1_md(context, EVP_sha256()) <= 0) {
        throw OpenSslError("Failed to set up RSA OAEP");
    }
}

} // namespace crypto
