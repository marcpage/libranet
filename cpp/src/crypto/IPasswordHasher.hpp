#pragma once

#include <cstddef>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

namespace crypto {

class IPasswordHasher {
public:
    using Ptr = std::unique_ptr<IPasswordHasher>;

    static Ptr Argon2id(
        uint32_t memory_cost_kib,
        uint32_t iterations,
        uint32_t parallelism,
        std::size_t salt_length,
        std::size_t hash_length);

    [[nodiscard]] virtual std::string hash_password(std::string_view password) const = 0;
    [[nodiscard]] virtual bool verify_password(std::string_view password, std::string_view hash) const = 0;

    virtual ~IPasswordHasher() = default;
    IPasswordHasher(const IPasswordHasher&) = delete;
    IPasswordHasher& operator=(const IPasswordHasher&) = delete;
    IPasswordHasher(IPasswordHasher&&) noexcept = default;
    IPasswordHasher& operator=(IPasswordHasher&&) noexcept = default;

protected:
    IPasswordHasher() = default;
};

} // namespace crypto
