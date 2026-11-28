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

    [[nodiscard]] static Ptr Sha256();

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
    IHasher(IHasher&&) noexcept = default;
    IHasher& operator=(IHasher&&) noexcept = default;

    IHasher(const IHasher&) = delete;
    IHasher& operator=(const IHasher&) = delete;

protected:
    IHasher() = default;
};

} // namespace crypto
