#pragma once

#include <array>
#include <cstddef>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include <openssl/err.h>
#include <openssl/evp.h>
#include <openssl/x509.h>

namespace crypto {

// OpenSSL takes bytes as unsigned char, and this library keeps them as
// std::byte or char. Each of the three may be read as any other, so a
// pointer to one is passed to OpenSSL as a pointer to unsigned char.
class OpenSslBytes {
public:
    [[nodiscard]] static unsigned char* of(std::vector<std::byte>& data) noexcept;
    // Where OpenSSL is to write offset bytes into data, which may be its end.
    [[nodiscard]] static unsigned char* of(std::vector<std::byte>& data, std::size_t offset);
    [[nodiscard]] static const unsigned char* of(const std::vector<std::byte>& data) noexcept;
    [[nodiscard]] static unsigned char* of(std::string& text) noexcept;
    [[nodiscard]] static const unsigned char* of(std::string_view text) noexcept;

    OpenSslBytes() = delete;
    ~OpenSslBytes() = delete;
    OpenSslBytes(const OpenSslBytes&) = delete;
    OpenSslBytes& operator=(const OpenSslBytes&) = delete;
    OpenSslBytes(OpenSslBytes&&) = delete;
    OpenSslBytes& operator=(OpenSslBytes&&) = delete;
};

// Frees an OpenSSL object with the function OpenSSL gives for its type.
template<typename Type, void (*free_object)(Type*)>
class OpenSslFree {
public:
    void operator()(Type* object) const noexcept;
};

// An OpenSSL object, freed when it goes out of scope.
template<typename Type, void (*free_object)(Type*)>
using OpenSslObject = std::unique_ptr<Type, OpenSslFree<Type, free_object>>;

// An OpenSSL key, freed when it goes out of scope.
using OpenSslKey = OpenSslObject<EVP_PKEY, EVP_PKEY_free>;

// Keys and other OpenSSL objects in DER, the binary form that PEM wraps. A
// key is read from all of the bytes given: bytes after it are refused.
class OpenSslDer {
public:
    // What an i2d function writes: the DER encoding of object.
    template<typename Type>
    [[nodiscard]] static std::vector<std::byte> write(const Type* object,
                                                      int (*encode)(const Type*, unsigned char**));

    // A private key in PKCS #8, or in an older form of its type's own, such
    // as PKCS #1 for RSA.
    [[nodiscard]] static OpenSslKey read_private_key(const std::vector<std::byte>& der);
    // A public key in SubjectPublicKeyInfo.
    [[nodiscard]] static OpenSslKey read_public_key(const std::vector<std::byte>& der);
    // An RSA public key in PKCS #1.
    [[nodiscard]] static OpenSslKey read_rsa_public_key(const std::vector<std::byte>& der);

    OpenSslDer() = delete;
    ~OpenSslDer() = delete;
    OpenSslDer(const OpenSslDer&) = delete;
    OpenSslDer& operator=(const OpenSslDer&) = delete;
    OpenSslDer(OpenSslDer&&) = delete;
    OpenSslDer& operator=(OpenSslDer&&) = delete;

private:
    // A d2i function, which reads a key from where next points, and moves
    // next past it. long, as the d2i functions take it:
    // NOLINTNEXTLINE(google-runtime-int)
    using KeyReader = EVP_PKEY* (*)(EVP_PKEY** key, const unsigned char** next, long length);

    [[nodiscard]] static OpenSslKey _read_key(const std::vector<std::byte>& der, KeyReader read,
                                              std::string_view what);
    // NOLINTNEXTLINE(google-runtime-int)
    static EVP_PKEY* _d2i_rsa_public_key(EVP_PKEY** key, const unsigned char** next, long length);
};

// A failed OpenSSL call: what was being done, and the last error OpenSSL
// queued for this thread. Making one clears the queue, so that a later
// failure does not report this one's errors.
class OpenSslError : public std::runtime_error {
public:
    explicit OpenSslError(std::string_view what);

private:
    [[nodiscard]] static std::string _describe(std::string_view what);
};

// NOLINTBEGIN(cppcoreguidelines-pro-type-reinterpret-cast)
inline unsigned char* OpenSslBytes::of(std::vector<std::byte>& data) noexcept {
    return reinterpret_cast<unsigned char*>(data.data());
}

inline unsigned char* OpenSslBytes::of(std::vector<std::byte>& data, std::size_t offset) {
    if (offset > data.size()) {
        throw std::out_of_range("Past the end of the bytes");
    }

    // NOLINTNEXTLINE(cppcoreguidelines-pro-bounds-pointer-arithmetic)
    return of(data) + offset;
}

inline const unsigned char* OpenSslBytes::of(const std::vector<std::byte>& data) noexcept {
    return reinterpret_cast<const unsigned char*>(data.data());
}

inline unsigned char* OpenSslBytes::of(std::string& text) noexcept {
    return reinterpret_cast<unsigned char*>(text.data());
}

inline const unsigned char* OpenSslBytes::of(std::string_view text) noexcept {
    return reinterpret_cast<const unsigned char*>(text.data());
}
// NOLINTEND(cppcoreguidelines-pro-type-reinterpret-cast)

template<typename Type, void (*free_object)(Type*)>
inline void OpenSslFree<Type, free_object>::operator()(Type* object) const noexcept {
    free_object(object);
}

template<typename Type>
inline std::vector<std::byte> OpenSslDer::write(const Type* object,
                                                int (*encode)(const Type*, unsigned char**)) {
    const auto length = encode(object, nullptr);

    if (length <= 0) {
        throw OpenSslError("Failed to encode in DER");
    }

    std::vector<std::byte> bytes(static_cast<std::size_t>(length));
    auto* next = OpenSslBytes::of(bytes);

    if (encode(object, &next) != length) {
        throw OpenSslError("Failed to encode in DER");
    }

    return bytes;
}

inline OpenSslKey OpenSslDer::read_private_key(const std::vector<std::byte>& der) {
    return _read_key(der, d2i_AutoPrivateKey, "Not a private key.");
}

inline OpenSslKey OpenSslDer::read_public_key(const std::vector<std::byte>& der) {
    return _read_key(der, d2i_PUBKEY, "Not a SubjectPublicKeyInfo public key.");
}

inline OpenSslKey OpenSslDer::read_rsa_public_key(const std::vector<std::byte>& der) {
    return _read_key(der, _d2i_rsa_public_key, "Not a PKCS #1 public key.");
}

inline OpenSslKey OpenSslDer::_read_key(const std::vector<std::byte>& der, KeyReader read,
                                        std::string_view what) {
    const auto* const start = OpenSslBytes::of(der);
    const auto* next = start;
    // NOLINTNEXTLINE(google-runtime-int)
    OpenSslKey key(read(nullptr, &next, static_cast<long>(der.size())));

    if (!key) {
        ERR_clear_error();
        throw std::invalid_argument(std::string(what));
    }

    if (static_cast<std::size_t>(next - start) != der.size()) {
        throw std::invalid_argument("Bytes after the key in its PEM");
    }

    return key;
}

// NOLINTBEGIN(google-runtime-int)
inline EVP_PKEY* OpenSslDer::_d2i_rsa_public_key(EVP_PKEY** key, const unsigned char** next,
                                                 long length) {
    return d2i_PublicKey(EVP_PKEY_RSA, key, next, length);
}
// NOLINTEND(google-runtime-int)

inline OpenSslError::OpenSslError(std::string_view what)
    : std::runtime_error(_describe(what)) {}

inline std::string OpenSslError::_describe(std::string_view what) {
    // ERR_error_string_n() writes at most this many characters, ending
    // with a null.
    constexpr auto error_text_size = 256;
    std::string description(what);
    const auto code = ERR_peek_last_error();

    if (code != 0) {
        std::array<char, error_text_size> text{};

        ERR_error_string_n(code, text.data(), text.size());
        description += ": ";
        description += text.data();
    }

    ERR_clear_error();
    return description;
}

} // namespace crypto
