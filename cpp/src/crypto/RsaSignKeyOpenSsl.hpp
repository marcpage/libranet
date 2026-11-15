#pragma once

#include <cstddef>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/rsa.h>

#include "ISignKey.hpp"
#include "OpenSsl.hpp"
#include "RsaPemOpenSsl.hpp"

namespace crypto {

// RSASSA-PSS with SHA-256, built on OpenSSL: MGF1 over SHA-256, and a salt
// as long as the hash, as Apple's Security framework and the Python node
// sign with.
class RsaSignKeyOpenSsl : public ISignKey {
public:
    [[nodiscard]] static ISignKey::Ptr from_pem(std::string_view pem);

    RsaSignKeyOpenSsl(OpenSslKey key, bool has_private);

    [[nodiscard]] Data sign(std::string_view data) const override;
    [[nodiscard]] bool verify(std::string_view data, const Data& signature) const override;

    [[nodiscard]] std::string public_pem() const override;
    [[nodiscard]] std::string private_pem() const override;
    [[nodiscard]] std::string id() const override;
    [[nodiscard]] bool has_private_key() const noexcept override;

    ~RsaSignKeyOpenSsl() override = default;
    RsaSignKeyOpenSsl(const RsaSignKeyOpenSsl&) = delete;
    RsaSignKeyOpenSsl& operator=(const RsaSignKeyOpenSsl&) = delete;
    RsaSignKeyOpenSsl(RsaSignKeyOpenSsl&&) noexcept = default;
    RsaSignKeyOpenSsl& operator=(RsaSignKeyOpenSsl&&) noexcept = default;

private:
    using MessageContext = OpenSslObject<EVP_MD_CTX, EVP_MD_CTX_free>;

    OpenSslKey m_key;
    bool m_has_private;

    static void use_pss(EVP_PKEY_CTX* context);
};

inline ISignKey::Ptr RsaSignKeyOpenSsl::from_pem(std::string_view pem) {
    auto [key, has_private] = RsaPemOpenSsl::from_pem(pem);

    return std::make_unique<RsaSignKeyOpenSsl>(std::move(key), has_private);
}

inline RsaSignKeyOpenSsl::RsaSignKeyOpenSsl(OpenSslKey key, bool has_private)
    : m_key(std::move(key)), m_has_private(has_private) {}

inline ISignKey::Data RsaSignKeyOpenSsl::sign(std::string_view data) const {
    if (!m_has_private) {
        throw std::runtime_error("RSA private key required for signing.");
    }

    const MessageContext context(EVP_MD_CTX_new());
    EVP_PKEY_CTX* key_context = nullptr;

    if (!context
        || EVP_DigestSignInit(context.get(), &key_context, EVP_sha256(), nullptr, m_key.get())
               != 1) {
        throw OpenSslError("Failed to start signing with an RSA key");
    }

    use_pss(key_context);

    Data signature(static_cast<std::size_t>(EVP_PKEY_get_size(m_key.get())));
    auto length = signature.size();

    if (EVP_DigestSign(context.get(), OpenSslBytes::of(signature), &length,
                       OpenSslBytes::of(data), data.size())
        != 1) {
        throw OpenSslError("RSA PSS signature creation failed");
    }

    signature.resize(length);
    return signature;
}

inline bool RsaSignKeyOpenSsl::verify(std::string_view data, const Data& signature) const {
    const MessageContext context(EVP_MD_CTX_new());
    EVP_PKEY_CTX* key_context = nullptr;

    if (!context
        || EVP_DigestVerifyInit(context.get(), &key_context, EVP_sha256(), nullptr, m_key.get())
               != 1) {
        throw OpenSslError("Failed to start verifying with an RSA key");
    }

    use_pss(key_context);

    const auto result = EVP_DigestVerify(context.get(), OpenSslBytes::of(signature),
                                         signature.size(), OpenSslBytes::of(data), data.size());

    // A signature that does not match leaves an error in OpenSSL's queue.
    ERR_clear_error();
    return result == 1;
}

inline std::string RsaSignKeyOpenSsl::public_pem() const {
    return RsaPemOpenSsl::public_pem(m_key.get());
}

inline std::string RsaSignKeyOpenSsl::private_pem() const {
    if (!m_has_private) {
        throw std::runtime_error("RSA private key not available.");
    }

    return RsaPemOpenSsl::private_pem(m_key.get());
}

inline std::string RsaSignKeyOpenSsl::id() const {
    return "rsa-pss-sha256";
}

inline bool RsaSignKeyOpenSsl::has_private_key() const noexcept {
    return m_has_private;
}

// The context belongs to the message context that made it.
inline void RsaSignKeyOpenSsl::use_pss(EVP_PKEY_CTX* context) {
    if (EVP_PKEY_CTX_set_rsa_padding(context, RSA_PKCS1_PSS_PADDING) <= 0
        || EVP_PKEY_CTX_set_rsa_mgf1_md(context, EVP_sha256()) <= 0
        || EVP_PKEY_CTX_set_rsa_pss_saltlen(context, RSA_PSS_SALTLEN_DIGEST) <= 0) {
        throw OpenSslError("Failed to set up RSA PSS");
    }
}

} // namespace crypto
