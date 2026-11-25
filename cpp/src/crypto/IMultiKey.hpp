#pragma once

#include <cstddef>
#include <memory>
#include <string>
#include <string_view>

#include "IEncryptKey.hpp"
#include "ISignKey.hpp"

namespace crypto {

class IMultiKey {
public:
    using Ptr = std::unique_ptr<IMultiKey>;

    [[nodiscard]] static Ptr Rsa(std::size_t key_bits);
    [[nodiscard]] static Ptr from_pem(std::string_view pem);

    [[nodiscard]] virtual ISignKey::Ptr RsaPssSha256() const = 0;
    [[nodiscard]] virtual IEncryptKey::Ptr RsaOaepSha256() const = 0;

    [[nodiscard]] virtual std::string public_pem() const = 0;
    [[nodiscard]] virtual std::string private_pem() const = 0;
    [[nodiscard]] virtual std::string id() const = 0;

    virtual ~IMultiKey() = default;
    IMultiKey(IMultiKey&&) noexcept = default;
    IMultiKey& operator=(IMultiKey&&) noexcept = default;

    IMultiKey(const IMultiKey&) = delete;
    IMultiKey& operator=(const IMultiKey&) = delete;

protected:
    IMultiKey() = default;
};

} // namespace crypto
