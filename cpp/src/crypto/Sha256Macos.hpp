#pragma once

#if __APPLE__

#include <algorithm>
#include <array>
#include <cstddef>
#include <memory>
#include <string_view>

#include <CommonCrypto/CommonDigest.h>

#include "IHasher.hpp"

namespace crypto {

class Sha256Macos : public IHasher {
public:
    Sha256Macos();
    ~Sha256Macos() override = default;
    Sha256Macos(const Sha256Macos&) = delete;
    Sha256Macos& operator=(const Sha256Macos&) = delete;
    Sha256Macos(Sha256Macos&&) noexcept = default;
    Sha256Macos& operator=(Sha256Macos&&) noexcept = default;

    void update(std::string_view data) override;
    void update(const Data& data) override;
    [[nodiscard]] Data digest(const Data& data) override;
    [[nodiscard]] std::size_t digest_size() const noexcept override;
    [[nodiscard]] std::size_t block_size() const noexcept override;
    [[nodiscard]] Ptr clone() const override;
    void reset() override;

private:
    CC_SHA256_CTX m_context{};

    void start();
};

inline Sha256Macos::Sha256Macos() {
    start();
}

inline void Sha256Macos::update(std::string_view data) {
    CC_SHA256_Update(&m_context, data.data(), static_cast<CC_LONG>(data.size()));
}

inline void Sha256Macos::update(const Data& data) {
    CC_SHA256_Update(&m_context, data.data(), static_cast<CC_LONG>(data.size()));
}

inline IHasher::Data Sha256Macos::digest(const Data& data) {
    if (!data.empty()) {
        update(data);
    }

    std::array<unsigned char, CC_SHA256_DIGEST_LENGTH> bytes{};
    Data result(bytes.size());

    CC_SHA256_Final(bytes.data(), &m_context);
    start();
    std::transform(bytes.begin(), bytes.end(), result.begin(),
                   [](unsigned char byte) { return static_cast<std::byte>(byte); });
    return result;
}

inline std::size_t Sha256Macos::digest_size() const noexcept {
    return CC_SHA256_DIGEST_LENGTH;
}

inline std::size_t Sha256Macos::block_size() const noexcept {
    return CC_SHA256_BLOCK_BYTES;
}

inline IHasher::Ptr Sha256Macos::clone() const {
    auto cloned = std::make_unique<Sha256Macos>();

    cloned->m_context = m_context;
    return cloned;
}

inline void Sha256Macos::reset() {
    start();
}

inline void Sha256Macos::start() {
    CC_SHA256_Init(&m_context);
}

} // namespace crypto

#endif // __APPLE__
