#pragma once

#include <cstddef>
#include <memory>
#include <string_view>

#include <openssl/evp.h>

#include "IHasher.hpp"
#include "OpenSsl.hpp"

namespace crypto {

// SHA-256, built on OpenSSL.
class Sha256OpenSsl : public IHasher {
public:
    Sha256OpenSsl();
    ~Sha256OpenSsl() override = default;
    Sha256OpenSsl(const Sha256OpenSsl&) = delete;
    Sha256OpenSsl& operator=(const Sha256OpenSsl&) = delete;
    Sha256OpenSsl(Sha256OpenSsl&&) noexcept = default;
    Sha256OpenSsl& operator=(Sha256OpenSsl&&) noexcept = default;

    void update(std::string_view data) override;
    void update(const Data& data) override;
    [[nodiscard]] Data digest(const Data& data) override;
    [[nodiscard]] std::size_t digest_size() const noexcept override;
    [[nodiscard]] std::size_t block_size() const noexcept override;
    [[nodiscard]] Ptr clone() const override;
    void reset() override;

private:
    using Context = OpenSslObject<EVP_MD_CTX, EVP_MD_CTX_free>;

    // SHA-256's sizes, in bytes.
    static constexpr std::size_t sha256_digest_size = 32;
    static constexpr std::size_t sha256_block_size = 64;

    Context m_context;

    void _start();
    void _add(const unsigned char* bytes, std::size_t size);
};

inline Sha256OpenSsl::Sha256OpenSsl() : m_context(EVP_MD_CTX_new()) {
    if (!m_context) {
        throw OpenSslError("Failed to start SHA-256");
    }

    _start();
}

inline void Sha256OpenSsl::update(std::string_view data) {
    _add(OpenSslBytes::of(data), data.size());
}

inline void Sha256OpenSsl::update(const Data& data) {
    _add(OpenSslBytes::of(data), data.size());
}

inline IHasher::Data Sha256OpenSsl::digest(const Data& data) {
    if (!data.empty()) {
        update(data);
    }

    Data result(sha256_digest_size);

    if (EVP_DigestFinal_ex(m_context.get(), OpenSslBytes::of(result), nullptr) != 1) {
        throw OpenSslError("SHA-256 failed");
    }

    _start();
    return result;
}

inline std::size_t Sha256OpenSsl::digest_size() const noexcept {
    return sha256_digest_size;
}

inline std::size_t Sha256OpenSsl::block_size() const noexcept {
    return sha256_block_size;
}

inline IHasher::Ptr Sha256OpenSsl::clone() const {
    auto cloned = std::make_unique<Sha256OpenSsl>();

    if (EVP_MD_CTX_copy_ex(cloned->m_context.get(), m_context.get()) != 1) {
        throw OpenSslError("Failed to copy SHA-256");
    }

    return cloned;
}

inline void Sha256OpenSsl::reset() {
    _start();
}

inline void Sha256OpenSsl::_start() {
    if (EVP_DigestInit_ex(m_context.get(), EVP_sha256(), nullptr) != 1) {
        throw OpenSslError("Failed to start SHA-256");
    }
}

inline void Sha256OpenSsl::_add(const unsigned char* bytes, std::size_t size) {
    if (EVP_DigestUpdate(m_context.get(), bytes, size) != 1) {
        throw OpenSslError("SHA-256 failed");
    }
}

} // namespace crypto
