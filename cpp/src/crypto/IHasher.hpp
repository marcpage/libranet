#pragma once

#include <cstddef>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

namespace crypto {

class IHasher {
public:
    using Ptr = std::unique_ptr<IHasher>;
    using Data = std::vector<std::byte>;

    static Ptr Sha256();

    virtual void update(std::string_view data) = 0;
    virtual void update(const Data& data) = 0;
    [[nodiscard]] virtual Data digest(const Data& data) = 0;
    [[nodiscard]] Data digest();
    [[nodiscard]] virtual std::string hex_digest(const Data& data);
    [[nodiscard]] std::string hex_digest();
    [[nodiscard]] virtual std::size_t digest_size() const noexcept = 0;
    [[nodiscard]] virtual std::size_t block_size() const noexcept = 0;
    [[nodiscard]] virtual Ptr clone() const = 0;
    virtual void reset() = 0;

    virtual ~IHasher() = default;
    IHasher(const IHasher&) = delete;
    IHasher& operator=(const IHasher&) = delete;
    IHasher(IHasher&&) noexcept = default;
    IHasher& operator=(IHasher&&) noexcept = default;

protected:
    IHasher() = default;
};

// The digest of what was added, with nothing more.
inline IHasher::Data IHasher::digest() {
    return digest(Data());
}

inline std::string IHasher::hex_digest(const Data& data) {
    constexpr std::string_view hex_chars = "0123456789abcdef";
    constexpr auto nibble_bits = 4U;
    constexpr auto nibble_mask = 0x0FU;
    const Data raw = digest(data);
    std::string hex;

    hex.reserve(raw.size() * 2);

    for (const std::byte byte : raw) {
        const auto value = std::to_integer<unsigned int>(byte);

        hex.push_back(hex_chars.at(value >> nibble_bits));
        hex.push_back(hex_chars.at(value & nibble_mask));
    }

    return hex;
}

inline std::string IHasher::hex_digest() {
    return hex_digest(Data());
}

} // namespace crypto
