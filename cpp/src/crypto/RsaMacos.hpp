#pragma once

#if __APPLE__

#include <array>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>

#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>

#include "CFObject.hpp"
#include "IEncryptKey.hpp"
#include "IMultiKey.hpp"
#include "ISignKey.hpp"
#include "RsaEncryptKeyMacos.hpp"
#include "RsaPemMacos.hpp"
#include "RsaSignKeyMacos.hpp"

namespace crypto {

class RsaMacos : public IMultiKey {
public:
    [[nodiscard]] static IMultiKey::Ptr from_pem(std::string_view pem);
    [[nodiscard]] static IMultiKey::Ptr for_bit_size(std::size_t key_bits);

    RsaMacos(SecKeyRef private_key, SecKeyRef public_key);

    [[nodiscard]] ISignKey::Ptr RsaPssSha256() const override;
    [[nodiscard]] IEncryptKey::Ptr RsaOaepSha256() const override;

    [[nodiscard]] std::string public_pem() const override;
    [[nodiscard]] std::string private_pem() const override;
    [[nodiscard]] std::string id() const override;

    ~RsaMacos() override = default;
    RsaMacos(RsaMacos&& other) noexcept = default;
    RsaMacos& operator=(RsaMacos&& other) noexcept = default;

    RsaMacos(const RsaMacos&) = delete;
    RsaMacos& operator=(const RsaMacos&) = delete;

private:
    CFObject<SecKeyRef> m_private_key;
    CFObject<SecKeyRef> m_public_key;

    friend class RsaSignKeyMacos;
    friend class RsaEncryptKeyMacos;
};

inline IMultiKey::Ptr RsaMacos::for_bit_size(std::size_t key_bits) {
    const CFObject<CFNumberRef> key_size_number(
        CFNumberCreate(kCFAllocatorDefault, kCFNumberSInt64Type, &key_bits),
        CFObject<CFNumberRef>::Policy::TakeOwnership);
    std::array<const void*, 2> keys{kSecAttrKeyType, kSecAttrKeySizeInBits};
    std::array<const void*, 2> values{kSecAttrKeyTypeRSA, key_size_number};
    const CFObject<CFDictionaryRef> parameters(
        CFDictionaryCreate(
            kCFAllocatorDefault,
            keys.data(),
            values.data(),
            static_cast<CFIndex>(keys.size()),
            &kCFTypeDictionaryKeyCallBacks,
            &kCFTypeDictionaryValueCallBacks),
        CFObject<CFDictionaryRef>::Policy::TakeOwnership);

    CFObject<CFErrorRef> error;
    const CFObject<SecKeyRef> private_key(
        SecKeyCreateRandomKey(parameters, error.put()),
        CFObject<SecKeyRef>::Policy::TakeOwnership);

    if (!private_key) {
        throw std::runtime_error("Failed to generate RSA key pair on macOS");
    }

    // Automatically derive the public counterpart from the generated private
    // key
    const CFObject<SecKeyRef> public_key(
        SecKeyCopyPublicKey(private_key),
        CFObject<SecKeyRef>::Policy::TakeOwnership);

    if (!public_key) {
        throw std::runtime_error("Failed to derive public key from generated RSA private key");
    }

    return std::make_unique<RsaMacos>(private_key.get(), public_key.get());
}

inline RsaMacos::RsaMacos(SecKeyRef private_key, SecKeyRef public_key)
    : m_private_key(private_key, CFObject<SecKeyRef>::Policy::Retain)
    , m_public_key(public_key, CFObject<SecKeyRef>::Policy::Retain) {
}

inline ISignKey::Ptr RsaMacos::RsaPssSha256() const {
    return std::make_unique<RsaSignKeyMacos>(m_private_key, m_public_key);
}

inline IEncryptKey::Ptr RsaMacos::RsaOaepSha256() const {
    return std::make_unique<RsaEncryptKeyMacos>(m_private_key, m_public_key);
}

inline std::string RsaMacos::public_pem() const {
    return RsaPemMacos::public_pem(m_public_key);
}

inline std::string RsaMacos::private_pem() const {
    return RsaPemMacos::private_pem(m_private_key);
}

inline std::string RsaMacos::id() const {
    return "rsa";
}

// cppcheck-suppress duplInheritedMember
inline IMultiKey::Ptr RsaMacos::from_pem(std::string_view pem) {
    auto [private_key, public_key] = RsaPemMacos::from_pem(pem);

    return std::make_unique<RsaMacos>(private_key.get(), public_key.get());
}

} // namespace crypto

#endif // __APPLE__
