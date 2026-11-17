#pragma once

#if __APPLE__

#include <array>
#include <cstddef>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>

#include "CFBytes.hpp"
#include "CFObject.hpp"
#include "Pem.hpp"
#include "RsaDer.hpp"

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

    RsaPemMacos() = delete;
    ~RsaPemMacos() = delete;
    RsaPemMacos(const RsaPemMacos&) = delete;
    RsaPemMacos& operator=(const RsaPemMacos&) = delete;
    RsaPemMacos(RsaPemMacos&&) = delete;
    RsaPemMacos& operator=(RsaPemMacos&&) = delete;

private:
    static std::pair<bool, bool> validate_and_classify_pem_label(std::string_view label);
    static std::vector<std::byte> pkcs1_from(std::string_view label,
                                             const std::vector<std::byte>& der_bytes);
    static CFObject<CFDataRef> create_cf_data(const std::vector<std::byte>& der_bytes);
    static CFObject<SecKeyRef> create_sec_key_from_data(CFDataRef data_ref,
                                                        bool is_private);
};

inline std::string RsaPemMacos::public_pem(SecKeyRef public_key) {
    if (public_key == nullptr) {
        throw std::runtime_error("RSA public key not available.");
    }

    CFObject<CFErrorRef> error;
    const CFObject<CFDataRef> data(SecKeyCopyExternalRepresentation(public_key, error.put()),
                                   CFObject<CFDataRef>::Policy::TakeOwnership);

    if (!data) {
        throw std::runtime_error("Failed to copy external representation of public key.");
    }

    // Apple gives an RSA key in PKCS #1, which "PUBLIC KEY" does not name.
    return Pem::wrap("PUBLIC KEY", RsaDer::spki_from_pkcs1(CFBytes::bytes_in(data)));
}

inline std::string RsaPemMacos::private_pem(SecKeyRef private_key) {
    if (private_key == nullptr) {
        throw std::runtime_error("RSA private key not available.");
    }

    CFObject<CFErrorRef> error;
    const CFObject<CFDataRef> data(SecKeyCopyExternalRepresentation(private_key, error.put()),
                                   CFObject<CFDataRef>::Policy::TakeOwnership);

    if (!data) {
        throw std::runtime_error(
            "Failed to copy external representation of private key.");
    }

    // Apple gives an RSA key in PKCS #1, which "PRIVATE KEY" does not name.
    return Pem::wrap("PRIVATE KEY", RsaDer::pkcs8_from_pkcs1(CFBytes::bytes_in(data)));
}

inline RsaPemMacos::KeyPair RsaPemMacos::from_pem(std::string_view pem) {
    auto [label, der_bytes] = Pem::unwrap(pem);
    auto [is_private, is_public] = validate_and_classify_pem_label(label);
    const auto data_ref = create_cf_data(pkcs1_from(label, der_bytes));
    CFObject<SecKeyRef> created_key = create_sec_key_from_data(data_ref, is_private);
    CFObject<SecKeyRef> private_key;
    CFObject<SecKeyRef> public_key;

    if (is_private) {
        private_key = std::move(created_key);
        public_key.reset(SecKeyCopyPublicKey(private_key),
                         CFObject<SecKeyRef>::Policy::TakeOwnership);
    } else {
        public_key = std::move(created_key);
    }

    return {std::move(private_key), std::move(public_key)};
}

inline std::pair<bool, bool> RsaPemMacos::validate_and_classify_pem_label(
    std::string_view label) {
    const bool is_private = (label == "PRIVATE KEY" || label == "RSA PRIVATE KEY");
    const bool is_public = (label == "PUBLIC KEY" || label == "RSA PUBLIC KEY");

    if (!is_private && !is_public) {
        throw std::invalid_argument("Unsupported or unrecognized PEM label: "
                                    + std::string(label));
    }

    return {is_private, is_public};
}

// Apple reads an RSA key only in PKCS #1, which "RSA PUBLIC KEY" and
// "RSA PRIVATE KEY" name, so it is first taken out of the forms that
// "PUBLIC KEY" and "PRIVATE KEY" name.
inline std::vector<std::byte> RsaPemMacos::pkcs1_from(std::string_view label,
                                                      const std::vector<std::byte>& der_bytes) {
    if (label == "PUBLIC KEY") {
        return RsaDer::pkcs1_from_spki(der_bytes);
    }

    if (label == "PRIVATE KEY") {
        return RsaDer::pkcs1_from_pkcs8(der_bytes);
    }

    return der_bytes;
}

inline CFObject<CFDataRef> RsaPemMacos::create_cf_data(
    const std::vector<std::byte>& der_bytes) {
    auto data_ref = CFBytes::of(der_bytes);

    if (!data_ref) {
        throw std::runtime_error("Failed to create CFData from PEM DER bytes.");
    }

    return data_ref;
}

inline CFObject<SecKeyRef> RsaPemMacos::create_sec_key_from_data(CFDataRef data_ref,
                                                                 bool is_private) {
    // How much of the error's description is shown, in bytes as UTF-8, with
    // the null that ends it.
    constexpr std::size_t most_description_bytes = 256;
    CFTypeRef key_class = is_private ? kSecAttrKeyClassPrivate : kSecAttrKeyClassPublic;
    std::array<const void*, 2> keys{kSecAttrKeyType, kSecAttrKeyClass};
    std::array<const void*, 2> values{kSecAttrKeyTypeRSA, key_class};

    const CFObject<CFDictionaryRef> attributes(CFDictionaryCreate(
                                                   kCFAllocatorDefault,
                                                   keys.data(),
                                                   values.data(),
                                                   static_cast<CFIndex>(keys.size()),
                                                   &kCFTypeDictionaryKeyCallBacks,
                                                   &kCFTypeDictionaryValueCallBacks),
                                               CFObject<CFDictionaryRef>::Policy::TakeOwnership);

    CFObject<CFErrorRef> error;
    CFObject<SecKeyRef> created_key(SecKeyCreateWithData(data_ref,
                                                         attributes, error.put()),
                                    CFObject<SecKeyRef>::Policy::TakeOwnership);

    if (!created_key) {
        std::string error_msg = "Failed to create SecKey from PEM data.";

        if (error) {
            const CFObject<CFStringRef> desc(CFErrorCopyDescription(error),
                                             CFObject<CFStringRef>::Policy::TakeOwnership);

            if (desc) {
                std::array<char, most_description_bytes> description{};

                if (CFStringGetCString(desc, description.data(),
                                       static_cast<CFIndex>(description.size()),
                                       kCFStringEncodingUTF8)
                    != 0U) {
                    error_msg += " Details: ";
                    error_msg += description.data();
                }
            }
        }

        throw std::runtime_error(error_msg);
    }

    return created_key;
}

} // namespace crypto

#endif // __APPLE__
