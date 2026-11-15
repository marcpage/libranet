#pragma once

#if __APPLE__

#include <stdexcept>
#include <string>
#include <vector>

#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>

#include "CFObject.hpp"
#include "Pem.hpp"

namespace crypto {

class RsaPemMacos {
public:
    struct KeyPair {
        CFObject<SecKeyRef> private_key;
        CFObject<SecKeyRef> public_key;
    };

    static std::string public_pem(SecKeyRef public_key);
    static std::string private_pem(SecKeyRef private_key);
    static KeyPair from_pem(std::string_view pem);

private:
    RsaPemMacos() = delete;
    ~RsaPemMacos() = delete;
    RsaPemMacos(const RsaPemMacos&) = delete;
    RsaPemMacos& operator=(const RsaPemMacos&) = delete;
    RsaPemMacos(RsaPemMacos&&) = delete;
    RsaPemMacos& operator=(RsaPemMacos&&) = delete;

    static std::pair<bool, bool> validate_and_classify_pem_label(std::string_view label);
    static CFObject<CFDataRef> create_cf_data(const std::vector<std::byte>& der_bytes);
    static CFObject<SecKeyRef> create_sec_key_from_data(CFDataRef data_ref,
                                                                        bool is_private);
};

inline std::string RsaPemMacos::public_pem(SecKeyRef public_key) {
    if (!public_key) {
        throw std::runtime_error("RSA public key not available.");
    }

    CFObject<CFErrorRef> error;
    CFObject<CFDataRef> data(SecKeyCopyExternalRepresentation(public_key, error.put()),
                             CFObject<CFDataRef>::Policy::TakeOwnership);

    if (!data) {
        throw std::runtime_error("Failed to copy external representation of public key.");
    }

    const auto* ptr = CFDataGetBytePtr(data);
    const auto length = CFDataGetLength(data);
    const std::vector<std::byte> bytes(reinterpret_cast<const std::byte*>(ptr),
                                  reinterpret_cast<const std::byte*>(ptr) + length);

    return Pem::wrap("PUBLIC KEY", bytes);
}

inline std::string RsaPemMacos::private_pem(SecKeyRef private_key) {
    if (!private_key) {
        throw std::runtime_error("RSA private key not available.");
    }

    CFObject<CFErrorRef> error;
    CFObject<CFDataRef> data(SecKeyCopyExternalRepresentation(private_key, error.put()),
                             CFObject<CFDataRef>::Policy::TakeOwnership);

    if (!data) {
        throw std::runtime_error(
                                "Failed to copy external representation of private key.");
    }

    const auto* ptr = CFDataGetBytePtr(data);
    const auto length = CFDataGetLength(data);
    const std::vector<std::byte> bytes(reinterpret_cast<const std::byte*>(ptr),
                                  reinterpret_cast<const std::byte*>(ptr) + length);

    return Pem::wrap("PRIVATE KEY", bytes);
}

inline RsaPemMacos::KeyPair RsaPemMacos::from_pem(std::string_view pem) {
    auto [label, der_bytes] = Pem::unwrap(pem);
    auto [is_private, is_public] = validate_and_classify_pem_label(label);
    CFObject<CFDataRef> data_ref = create_cf_data(der_bytes);
    CFObject<SecKeyRef> created_key = create_sec_key_from_data(data_ref, is_private);
    CFObject<SecKeyRef> private_key;
    CFObject<SecKeyRef> public_key;

    if (is_private) {
        private_key = std::move(created_key);
        public_key.reset(SecKeyCopyPublicKey(private_key),
                                            CFObject<SecKeyRef>::Policy::TakeOwnership);
    } else {
        public_key = created_key;
    }

    return {std::move(private_key), std::move(public_key)};
}

inline std::pair<bool, bool> RsaPemMacos::validate_and_classify_pem_label(
                                                                std::string_view label) {
    bool is_private = (label == "PRIVATE KEY" || label == "RSA PRIVATE KEY");
    bool is_public = (label == "PUBLIC KEY" || label == "RSA PUBLIC KEY");

    if (!is_private && !is_public) {
        throw std::invalid_argument("Unsupported or unrecognized PEM label: "
                                        + std::string(label));
    }

    return {is_private, is_public};
}

inline CFObject<CFDataRef> RsaPemMacos::create_cf_data(
                                                const std::vector<std::byte>& der_bytes) {
    CFObject<CFDataRef> data_ref(CFDataCreate(kCFAllocatorDefault,
                                         reinterpret_cast<const UInt8*>(der_bytes.data()),
                                         static_cast<CFIndex>(der_bytes.size())),
                                CFObject<CFDataRef>::Policy::TakeOwnership);

    if (!data_ref) {
        throw std::runtime_error("Failed to create CFData from PEM DER bytes.");
    }

    return data_ref;
}

inline CFObject<SecKeyRef> RsaPemMacos::create_sec_key_from_data(CFDataRef data_ref,
                                                                        bool is_private) {
    CFTypeRef key_class = is_private ? kSecAttrKeyClassPrivate : kSecAttrKeyClassPublic;
    const void* keys[] = { kSecAttrKeyType, kSecAttrKeyClass };
    const void* values[] = { kSecAttrKeyTypeRSA, key_class };

    CFObject<CFDictionaryRef> attributes(CFDictionaryCreate(
                                                kCFAllocatorDefault,
                                                keys,
                                                values,
                                                2,
                                                &kCFTypeDictionaryKeyCallBacks,
                                                &kCFTypeDictionaryValueCallBacks
                                            ),
                                        CFObject<CFDictionaryRef>::Policy::TakeOwnership);

    CFObject<CFErrorRef> error;
    CFObject<SecKeyRef> created_key(SecKeyCreateWithData(data_ref,
                                                                attributes, error.put()),
                                    CFObject<SecKeyRef>::Policy::TakeOwnership);

    if (!created_key) {
        std::string error_msg = "Failed to create SecKey from PEM data.";

        if (error) {
            CFObject<CFStringRef> desc(CFErrorCopyDescription(error),
                                        CFObject<CFStringRef>::Policy::TakeOwnership);

            if (desc) {
                char buf[256];

                if (CFStringGetCString(desc, buf, sizeof(buf), kCFStringEncodingUTF8)) {
                    error_msg += " Details: ";
                    error_msg += buf;
                }
            }
        }

        throw std::runtime_error(error_msg);
    }

    return created_key;
}

} // crypto

#endif // __APPLE__

