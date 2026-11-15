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
    [[nodiscard]] virtual Data digest(const Data& data=Data()) = 0;
    [[nodiscard]] virtual std::string hex_digest(const Data& data=Data());
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

inline std::string IHasher::hex_digest(const Data& data) {
    Data raw = digest(data);
    constexpr char hex_chars[] = "0123456789abcdef";
    std::string hex;

    hex.reserve(raw.size() * 2);

    for (std::byte b : raw) {
        const auto val = static_cast<unsigned char>(b);

        hex.push_back(hex_chars[val >> 4]);
        hex.push_back(hex_chars[val & 0x0F]);
    }

    return hex;
}

} // namespace crypto
