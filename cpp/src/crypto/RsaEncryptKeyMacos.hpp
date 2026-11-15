#pragma once

#if __APPLE__

#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "CFObject.hpp"
#include "IEncryptKey.hpp"

namespace crypto {

class RsaEncryptKeyMacos : public IEncryptKey {
public:
    static IMultiKey::Ptr from_pem(std::string_view pem);

    RsaEncryptKeyMacos(SecKeyRef private_key, SecKeyRef public_key);
    ~RsaEncryptKeyMacos() override = default;

    RsaEncryptKeyMacos(const RsaEncryptKeyMacos&) = delete;
    RsaEncryptKeyMacos& operator=(const RsaEncryptKeyMacos&) = delete;
    RsaEncryptKeyMacos(RsaEncryptKeyMacos&&) noexcept = default;
    RsaEncryptKeyMacos& operator=(RsaEncryptKeyMacos&&) noexcept = default;

    [[nodiscard]] Data encrypt(std::string_view plaintext, const InitializationVector& vector) const override;
    [[nodiscard]] std::string decrypt(const Data& ciphertext, const InitializationVector& vector) const override;

private:
    CFObject<SecKeyRef> m_private_key;
    CFObject<SecKeyRef> m_public_key;
};

inline RsaEncryptKeyMacos::RsaEncryptKeyMacos(SecKeyRef private_key, SecKeyRef public_key)
    : m_private_key(private_key, CFObject<SecKeyRef>::Policy::Retain)
    , m_public_key(public_key, CFObject<SecKeyRef>::Policy::Retain) {}

inline IEncryptKey::Data RsaEncryptKeyMacos::encrypt(std::string_view plaintext, const InitializationVector& /*vector*/) const {
    if (!m_public_key) {
        throw std::runtime_error("RSA public key required for encryption.");
    }
    CFObject<CFDataRef> input_data(CFDataCreate(kCFAllocatorDefault,
                                                reinterpret_cast<const UInt8*>(plaintext.data()),
                                                static_cast<CFIndex>(plaintext.size())),
                                   CFObject<CFDataRef>::Policy::TakeOwnership);

    if (!input_data) {
        throw std::runtime_error("Failed to create CFData for encryption.");
    }

    CFObject<CFErrorRef> error;
    CFObject<CFDataRef> cipher_data(SecKeyCreateEncryptedData(m_public_key,
                                                              kSecKeyAlgorithmRSAEncryptionOAEPSHA256,
                                                              input_data,
                                                              error.put()),
                                    CFObject<CFDataRef>::Policy::TakeOwnership);

    if (!cipher_data) {
        throw std::runtime_error("RSA OAEP encryption failed.");
    }

    const auto* ptr = CFDataGetBytePtr(cipher_data);
    const auto length = CFDataGetLength(cipher_data);
    Data result(reinterpret_cast<const std::byte*>(ptr),
                reinterpret_cast<const std::byte*>(ptr) + length);

    return result;
}

inline std::string RsaEncryptKeyMacos::decrypt(const Data& ciphertext, const InitializationVector& /*vector*/) const {
    if (!m_private_key) {
        throw std::runtime_error("RSA private key required for decryption.");
    }

    CFObject<CFDataRef> cipher_data(CFDataCreate(kCFAllocatorDefault,
                                                 reinterpret_cast<const UInt8*>(ciphertext.data()),
                                                 static_cast<CFIndex>(ciphertext.size())),
                                    CFObject<CFDataRef>::Policy::TakeOwnership);

    if (!cipher_data) {
        throw std::runtime_error("Failed to create CFData for decryption.");
    }

    CFObject<CFErrorRef> error;
    CFObject<CFDataRef> plain_data(SecKeyCreateDecryptedData(m_private_key,
                                                             kSecKeyAlgorithmRSAEncryptionOAEPSHA256,
                                                             cipher_data,
                                                             error.put()),
                                   CFObject<CFDataRef>::Policy::TakeOwnership);

    if (!plain_data) {
        throw std::runtime_error("RSA OAEP decryption failed.");
    }

    const auto* ptr = CFDataGetBytePtr(plain_data);
    const auto length = CFDataGetLength(plain_data);
    std::string result(reinterpret_cast<const char*>(ptr), static_cast<std::size_t>(length));

    return result;
}

} // namespace crypto

#endif // __APPLE__
