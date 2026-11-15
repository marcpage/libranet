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
#include "ISignKey.hpp"
#include "RsaPemMacos.hpp"

namespace crypto {

class RsaSignKeyMacos : public ISignKey {
public:
    [[nodiscard]] static ISignKey::Ptr from_pem(std::string_view pem);

    RsaSignKeyMacos(SecKeyRef private_key, SecKeyRef public_key);

    [[nodiscard]] Data sign(std::string_view data) const override;
    [[nodiscard]] bool verify(std::string_view data, const Data& signature) const override;

    [[nodiscard]] std::string public_pem() const override;
    [[nodiscard]] std::string private_pem() const override;
    [[nodiscard]] std::string id() const override;
    [[nodiscard]] bool has_private_key() const noexcept override;

    ~RsaSignKeyMacos() override = default;
    RsaSignKeyMacos(const RsaSignKeyMacos&) = delete;
    RsaSignKeyMacos& operator=(const RsaSignKeyMacos&) = delete;
    RsaSignKeyMacos(RsaSignKeyMacos&&) noexcept = default;
    RsaSignKeyMacos& operator=(RsaSignKeyMacos&&) noexcept = default;

private:
    CFObject<SecKeyRef> m_private_key;
    CFObject<SecKeyRef> m_public_key;
};

inline RsaSignKeyMacos::RsaSignKeyMacos(SecKeyRef private_key, SecKeyRef public_key)
    : m_private_key(private_key, CFObject<SecKeyRef>::Policy::Retain)
    , m_public_key(public_key, CFObject<SecKeyRef>::Policy::Retain) {}

inline ISignKey::Data RsaSignKeyMacos::sign(std::string_view data) const {
    if (!m_private_key) {
        throw std::runtime_error("RSA private key required for signing.");
    }

    CFObject<CFDataRef> input_data(CFDataCreate(kCFAllocatorDefault,
                                                reinterpret_cast<const UInt8*>(data.data()),
                                                static_cast<CFIndex>(data.size())),
                                   CFObject<CFDataRef>::Policy::TakeOwnership);

    if (!input_data) {
        throw std::runtime_error("Failed to create CFData for signing.");
    }

    CFObject<CFErrorRef> error;
    CFObject<CFDataRef> sig_data(SecKeyCreateSignature(m_private_key,
                                                       kSecKeyAlgorithmRSASignatureMessagePSSSHA256,
                                                       input_data,
                                                       error.put()),
                                 CFObject<CFDataRef>::Policy::TakeOwnership);

    if (!sig_data) {
        throw std::runtime_error("RSA PSS signature creation failed.");
    }

    const auto* ptr = CFDataGetBytePtr(sig_data);
    const auto length = CFDataGetLength(sig_data);
    Data result(reinterpret_cast<const std::byte*>(ptr),
                reinterpret_cast<const std::byte*>(ptr) + length);
    return result;
}

inline bool RsaSignKeyMacos::verify(std::string_view data, const Data& signature) const {
    if (!m_public_key) {
        throw std::runtime_error("RSA public key required for verification.");
    }

    CFObject<CFDataRef> input_data(CFDataCreate(kCFAllocatorDefault,
                                                reinterpret_cast<const UInt8*>(data.data()),
                                                static_cast<CFIndex>(data.size())),
                                   CFObject<CFDataRef>::Policy::TakeOwnership);
    CFObject<CFDataRef> sig_data(CFDataCreate(kCFAllocatorDefault,
                                              reinterpret_cast<const UInt8*>(signature.data()),
                                              static_cast<CFIndex>(signature.size())),
                                 CFObject<CFDataRef>::Policy::TakeOwnership);

    if (!input_data || !sig_data) {
        return false;
    }

    CFObject<CFErrorRef> error;
    bool valid = SecKeyVerifySignature(m_public_key,
                                       kSecKeyAlgorithmRSASignatureMessagePSSSHA256,
                                       input_data,
                                       sig_data,
                                       error.put());

    return valid;
}

inline std::string RsaSignKeyMacos::public_pem() const {
    return RsaPemMacos::public_pem(m_public_key);
}

inline std::string RsaSignKeyMacos::private_pem() const {
    return RsaPemMacos::private_pem(m_private_key);
}

inline std::string RsaSignKeyMacos::id() const {
    return "rsa-pss-sha256";
}

inline bool RsaSignKeyMacos::has_private_key() const noexcept {
    return m_private_key != nullptr;
}

inline ISignKey::Ptr RsaSignKeyMacos::from_pem(std::string_view pem) {
    auto [private_key, public_key] = RsaPemMacos::from_pem(pem);

    return std::make_unique<RsaSignKeyMacos>(private_key.release(), public_key.release());
}

} // namespace crypto

#endif // __APPLE__
