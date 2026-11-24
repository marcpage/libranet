#pragma once

#include <cstddef>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

namespace crypto {

class ISignKey {
public:
    using Ptr = std::unique_ptr<ISignKey>;
    using Data = std::vector<std::byte>;

    [[nodiscard]] static Ptr Ed25519();
    [[nodiscard]] static Ptr from_pem(std::string_view pem);

    [[nodiscard]] virtual Data sign(std::string_view data) const = 0;
    [[nodiscard]] virtual bool verify(std::string_view data,
                                      const Data& signature) const = 0;

    [[nodiscard]] virtual std::string public_pem() const = 0;
    [[nodiscard]] virtual std::string private_pem() const = 0;
    [[nodiscard]] virtual std::string id() const = 0;
    [[nodiscard]] virtual bool has_private_key() const noexcept = 0;

    virtual ~ISignKey() = default;
    ISignKey(ISignKey&&) noexcept = default;
    ISignKey& operator=(ISignKey&&) noexcept = default;

    ISignKey(const ISignKey&) = delete;
    ISignKey& operator=(const ISignKey&) = delete;

protected:
    ISignKey() = default;
};

} // namespace crypto
