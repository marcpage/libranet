#pragma once

#include <cstddef>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include <openssl/evp.h>
#include <openssl/rsa.h>

#include "IEncryptKey.hpp"
#include "IMultiKey.hpp"
#include "ISignKey.hpp"
#include "OpenSsl.hpp"
#include "RsaEncryptKeyOpenSsl.hpp"
#include "RsaPemOpenSsl.hpp"
#include "RsaSignKeyOpenSsl.hpp"

namespace crypto {

// An RSA key, built on OpenSSL, which signs and encrypts. The keys it gives
// for each share its one OpenSSL key.
class RsaOpenSsl : public IMultiKey {
public:
    [[nodiscard]] static IMultiKey::Ptr from_pem(std::string_view pem);
    [[nodiscard]] static IMultiKey::Ptr for_bit_size(std::size_t key_bits);

    RsaOpenSsl(OpenSslKey key, bool has_private);

    [[nodiscard]] ISignKey::Ptr RsaPssSha256() const override;
    [[nodiscard]] IEncryptKey::Ptr RsaOaepSha256() const override;

    [[nodiscard]] std::string public_pem() const override;
    [[nodiscard]] std::string private_pem() const override;
    [[nodiscard]] std::string id() const override;

    ~RsaOpenSsl() override = default;
    RsaOpenSsl(const RsaOpenSsl&) = delete;
    RsaOpenSsl& operator=(const RsaOpenSsl&) = delete;
    RsaOpenSsl(RsaOpenSsl&& other) noexcept = default;
    RsaOpenSsl& operator=(RsaOpenSsl&& other) noexcept = default;

private:
    OpenSslKey m_key;
    // Mistaken: the constructor sets m_has_private. cppcheck loses the
    // class's constructors in a file that names it with
    // `using crypto::RsaOpenSsl;`.
    // cppcheck-suppress uninitMemberVarNoCtor
    bool m_has_private;

    [[nodiscard]] OpenSslKey shared() const;
};

inline IMultiKey::Ptr RsaOpenSsl::for_bit_size(std::size_t key_bits) {
    if (key_bits > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
        throw std::invalid_argument("RSA key size out of range.");
    }

    const OpenSslObject<EVP_PKEY_CTX, EVP_PKEY_CTX_free> context(
        EVP_PKEY_CTX_new_from_name(nullptr, "RSA", nullptr));
    EVP_PKEY* key = nullptr;

    if (!context || EVP_PKEY_keygen_init(context.get()) != 1
        || EVP_PKEY_CTX_set_rsa_keygen_bits(context.get(), static_cast<int>(key_bits)) <= 0
        || EVP_PKEY_generate(context.get(), &key) != 1) {
        throw OpenSslError("Failed to generate an RSA key");
    }

    return std::make_unique<RsaOpenSsl>(OpenSslKey(key), true);
}

inline IMultiKey::Ptr RsaOpenSsl::from_pem(std::string_view pem) {
    auto [key, has_private] = RsaPemOpenSsl::from_pem(pem);

    return std::make_unique<RsaOpenSsl>(std::move(key), has_private);
}

inline RsaOpenSsl::RsaOpenSsl(OpenSslKey key, bool has_private)
    : m_key(std::move(key)), m_has_private(has_private) {}

inline ISignKey::Ptr RsaOpenSsl::RsaPssSha256() const {
    return std::make_unique<RsaSignKeyOpenSsl>(shared(), m_has_private);
}

inline IEncryptKey::Ptr RsaOpenSsl::RsaOaepSha256() const {
    return std::make_unique<RsaEncryptKeyOpenSsl>(shared(), m_has_private);
}

inline std::string RsaOpenSsl::public_pem() const {
    return RsaPemOpenSsl::public_pem(m_key.get());
}

inline std::string RsaOpenSsl::private_pem() const {
    if (!m_has_private) {
        throw std::runtime_error("RSA private key not available.");
    }

    return RsaPemOpenSsl::private_pem(m_key.get());
}

inline std::string RsaOpenSsl::id() const {
    return "rsa";
}

// Another reference to the key: OpenSSL frees it once the last one goes.
inline OpenSslKey RsaOpenSsl::shared() const {
    if (EVP_PKEY_up_ref(m_key.get()) != 1) {
        throw OpenSslError("Failed to share an RSA key");
    }

    return OpenSslKey(m_key.get());
}

} // namespace crypto
