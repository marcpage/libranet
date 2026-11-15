#pragma once

#include <array>
#include <cstddef>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include <openssl/err.h>

namespace crypto {

// OpenSSL takes bytes as unsigned char, and this library keeps them as
// std::byte or char. Each of the three may be read as any other, so a
// pointer to one is passed to OpenSSL as a pointer to unsigned char.
class OpenSslBytes {
public:
    [[nodiscard]] static unsigned char* of(std::vector<std::byte>& data) noexcept;
    [[nodiscard]] static const unsigned char* of(const std::vector<std::byte>& data) noexcept;
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

// A failed OpenSSL call: what was being done, and the last error OpenSSL
// queued for this thread. Making one clears the queue, so that a later
// failure does not report this one's errors.
class OpenSslError : public std::runtime_error {
public:
    explicit OpenSslError(std::string_view what);

private:
    [[nodiscard]] static std::string describe(std::string_view what);
};

// NOLINTBEGIN(cppcoreguidelines-pro-type-reinterpret-cast)
inline unsigned char* OpenSslBytes::of(std::vector<std::byte>& data) noexcept {
    return reinterpret_cast<unsigned char*>(data.data());
}

inline const unsigned char* OpenSslBytes::of(const std::vector<std::byte>& data) noexcept {
    return reinterpret_cast<const unsigned char*>(data.data());
}

inline const unsigned char* OpenSslBytes::of(std::string_view text) noexcept {
    return reinterpret_cast<const unsigned char*>(text.data());
}
// NOLINTEND(cppcoreguidelines-pro-type-reinterpret-cast)

template<typename Type, void (*free_object)(Type*)>
inline void OpenSslFree<Type, free_object>::operator()(Type* object) const noexcept {
    free_object(object);
}

inline OpenSslError::OpenSslError(std::string_view what)
    : std::runtime_error(describe(what)) {}

inline std::string OpenSslError::describe(std::string_view what) {
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
