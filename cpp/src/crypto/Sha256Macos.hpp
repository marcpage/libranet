#pragma once

#if __APPLE__

#include "IHasher.hpp"
#include <CommonCrypto/CommonDigest.h>
#include <cstring>
#include <stdexcept>

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
    [[nodiscard]] Data digest(const Data& data = Data()) override;
    [[nodiscard]] std::size_t digest_size() const noexcept override;
    [[nodiscard]] std::size_t block_size() const noexcept override;
    [[nodiscard]] Ptr clone() const override;
    void reset() override;

private:
    CC_SHA256_CTX ctx_;
};

inline Sha256Macos::Sha256Macos() {
    reset();
}

inline void Sha256Macos::update(std::string_view data) {
    CC_SHA256_Update(&ctx_, data.data(), static_cast<CC_LONG>(data.size()));
}

inline void Sha256Macos::update(const Data& data) {
    CC_SHA256_Update(&ctx_, data.data(), static_cast<CC_LONG>(data.size()));
}

inline IHasher::Data Sha256Macos::digest(const Data& data) {
    if (!data.empty()) {
        update(data);
    }

    Data result(CC_SHA256_DIGEST_LENGTH);

    CC_SHA256_Final(reinterpret_cast<unsigned char*>(result.data()), &ctx_);
    reset();
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

    cloned->ctx_ = this->ctx_;
    return cloned;
}

inline void Sha256Macos::reset() {
    CC_SHA256_Init(&ctx_);
}

} // namespace crypto

#endif // __APPLE__
