#pragma once

#if __APPLE__

#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "CFBytes.hpp"
#include "CFObject.hpp"
#include "IEncryptKey.hpp"

namespace crypto {

class RsaEncryptKeyMacos : public IEncryptKey {
public:
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

inline IEncryptKey::Data RsaEncryptKeyMacos::encrypt(
    std::string_view plaintext, [[maybe_unused]] const InitializationVector& vector) const {
    if (!m_public_key) {
        throw std::runtime_error("RSA public key required for encryption.");
    }

    const auto input_data = CFBytes::of(plaintext);

    if (!input_data) {
        throw std::runtime_error("Failed to create CFData for encryption.");
    }

    CFObject<CFErrorRef> error;
    const CFObject<CFDataRef> cipher_data(
        SecKeyCreateEncryptedData(m_public_key, kSecKeyAlgorithmRSAEncryptionOAEPSHA256,
                                  input_data, error.put()),
        CFObject<CFDataRef>::Policy::TakeOwnership);

    if (!cipher_data) {
        throw std::runtime_error("RSA OAEP encryption failed.");
    }

    return CFBytes::bytes_in(cipher_data);
}

inline std::string RsaEncryptKeyMacos::decrypt(
    const Data& ciphertext, [[maybe_unused]] const InitializationVector& vector) const {
    if (!m_private_key) {
        throw std::runtime_error("RSA private key required for decryption.");
    }

    const auto cipher_data = CFBytes::of(ciphertext);

    if (!cipher_data) {
        throw std::runtime_error("Failed to create CFData for decryption.");
    }

    CFObject<CFErrorRef> error;
    const CFObject<CFDataRef> plain_data(
        SecKeyCreateDecryptedData(m_private_key, kSecKeyAlgorithmRSAEncryptionOAEPSHA256,
                                  cipher_data, error.put()),
        CFObject<CFDataRef>::Policy::TakeOwnership);

    if (!plain_data) {
        throw std::runtime_error("RSA OAEP decryption failed.");
    }

    return CFBytes::text_in(plain_data);
}

} // namespace crypto

#endif // __APPLE__
