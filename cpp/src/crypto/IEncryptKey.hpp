#pragma once

#include <cstddef>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

namespace crypto {

class IEncryptKey {
public:
    using Ptr = std::unique_ptr<IEncryptKey>;
    using Data = std::vector<std::byte>;
    using InitializationVector = std::vector<std::byte>;
    enum class AesMode { Cbc, Ctr };
    enum class AesPadding { Pkcs7, None };

    static Ptr Aes(const Data& raw_key, AesMode mode, AesPadding padding);

    [[nodiscard]] virtual Data encrypt(std::string_view plaintext,
                                            const InitializationVector& vector) const = 0;
    [[nodiscard]] virtual std::string decrypt(const Data& ciphertext,
                                            const InitializationVector& vector) const = 0;

    virtual ~IEncryptKey() = default;
    IEncryptKey(const IEncryptKey&) = delete;
    IEncryptKey& operator=(const IEncryptKey&) = delete;
    IEncryptKey(IEncryptKey&&) noexcept = default;
    IEncryptKey& operator=(IEncryptKey&&) noexcept = default;

protected:
    IEncryptKey() = default;
};

} // namespace crypto
