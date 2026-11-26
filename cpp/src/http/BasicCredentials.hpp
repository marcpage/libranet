#pragma once

#include <algorithm>
#include <array>
#include <cctype>
#include <cstddef>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "Headers.hpp"
#include "crypto/Base64.hpp"

namespace http {

// The username and password an Authorization field gives in the Basic scheme
// (RFC 7617 §2), in UTF-8, the one charset it may name (§2.1). The password
// is wiped from memory as it goes.
class BasicCredentials {
public:
    // The field that carries them (RFC 9110 §11.6.2).
    static constexpr std::string_view field_name = "Authorization";

    // The credentials an Authorization field's value gives, whatever the case
    // of its scheme, or none when it names another scheme, or what it gives
    // is not base64, not UTF-8, or holds no ':'.
    [[nodiscard]] static std::optional<BasicCredentials> parse(std::string_view text);

    // From the username and password joined by a ':', as RFC 7617 §2 joins
    // them. Throws std::invalid_argument for user_pass with no ':', or not in
    // UTF-8.
    explicit BasicCredentials(std::string_view user_pass);
    ~BasicCredentials();

    // Up to the first ':', which a username cannot hold (RFC 7617 §2).
    [[nodiscard]] const std::string& username() const noexcept;
    [[nodiscard]] const std::string& password() const noexcept;
    // The username and password joined by a ':'.
    [[nodiscard]] std::string user_pass() const;
    // As an Authorization field's value.
    [[nodiscard]] std::string to_string() const;

    BasicCredentials(BasicCredentials&&) noexcept = default;
    BasicCredentials& operator=(BasicCredentials&&) noexcept = default;

    BasicCredentials(const BasicCredentials&) = delete;
    BasicCredentials& operator=(const BasicCredentials&) = delete;

private:
    // The bytes a character in UTF-8 may start with, first_low to first_high;
    // those its second byte may be, second_low to second_high; and how many
    // bytes it takes. The second bytes leave out a character written longer
    // than it need be, one past U+10FFFF, and the surrogates UTF-16 takes:
    // RFC 3629 §4.
    struct Utf8Start {
        unsigned char first_low;
        unsigned char first_high;
        unsigned char second_low;
        unsigned char second_high;
        std::size_t length;
    };

    // As RFC 7617 §2 writes it; it is read whatever its case (RFC 9110
    // §11.1).
    static constexpr std::string_view basic_scheme = "Basic";
    static constexpr std::array utf8_starts{
        Utf8Start{0x00U, 0x7FU, 0x00U, 0x00U, 1},
        Utf8Start{0xC2U, 0xDFU, 0x80U, 0xBFU, 2},
        Utf8Start{0xE0U, 0xE0U, 0xA0U, 0xBFU, 3},
        Utf8Start{0xE1U, 0xECU, 0x80U, 0xBFU, 3},
        Utf8Start{0xEDU, 0xEDU, 0x80U, 0x9FU, 3},
        Utf8Start{0xEEU, 0xEFU, 0x80U, 0xBFU, 3},
        Utf8Start{0xF0U, 0xF0U, 0x90U, 0xBFU, 4},
        Utf8Start{0xF1U, 0xF3U, 0x80U, 0xBFU, 4},
        Utf8Start{0xF4U, 0xF4U, 0x80U, 0x8FU, 4},
    };
    // What each byte of a character in UTF-8 after its second may be.
    static constexpr unsigned char utf8_tail_low = 0x80U;
    static constexpr unsigned char utf8_tail_high = 0xBFU;

    std::string m_username;
    std::string m_password;

    [[nodiscard]] static bool _is_basic(std::string_view scheme);
    [[nodiscard]] static std::optional<std::string> _decode(std::string_view encoded);
    [[nodiscard]] static std::string_view _checked(std::string_view user_pass);
    [[nodiscard]] static bool _is_utf8(std::string_view text);
    [[nodiscard]] static std::size_t _utf8_length(std::string_view text);
    [[nodiscard]] static bool _has_rest_of(std::string_view text, const Utf8Start& start);
};

// A field that cannot be read gives none, so that it never stands for
// credentials that were not sent.
inline std::optional<BasicCredentials> BasicCredentials::parse(std::string_view text) {
    const auto space = std::min(text.find(' '), text.size());

    if (!_is_basic(text.substr(0, space))) {
        return std::nullopt;
    }

    const auto user_pass = _decode(Headers::trim_whitespace(text.substr(space)));

    if (!user_pass) {
        return std::nullopt;
    }

    try {
        return BasicCredentials(*user_pass);
    } catch (const std::invalid_argument&) {
        // No ':', or not UTF-8.
        return std::nullopt;
    }
}

inline BasicCredentials::BasicCredentials(std::string_view user_pass)
    : m_username(_checked(user_pass).substr(0, user_pass.find(':')))
    , m_password(user_pass.substr(m_username.size() + 1)) {}

// The password is wiped from memory once it is no longer needed, with all the
// room it had, where a password moved out of it leaves bytes behind.
inline BasicCredentials::~BasicCredentials() {
    m_password.resize(m_password.capacity());

    // Stored through a volatile, which the compiler cannot leave out as a
    // store that is never read.
    for (volatile char& character : m_password) {
        character = '\0';
    }
}

inline const std::string& BasicCredentials::username() const noexcept {
    return m_username;
}

inline const std::string& BasicCredentials::password() const noexcept {
    return m_password;
}

inline std::string BasicCredentials::user_pass() const {
    return m_username + ":" + m_password;
}

inline std::string BasicCredentials::to_string() const {
    return std::string(basic_scheme) + " " + crypto::Base64::encode(user_pass());
}

inline bool BasicCredentials::_is_basic(std::string_view scheme) {
    return std::equal(scheme.begin(), scheme.end(), basic_scheme.begin(), basic_scheme.end(),
                      [](unsigned char left, unsigned char right) {
        return std::tolower(left) == std::tolower(right);
    });
}

// The text encoded gives in base64, or none when it is not base64.
// Base64::decode() takes some text that is not, such as padding before the
// end, which then reads back as other text.
inline std::optional<std::string> BasicCredentials::_decode(std::string_view encoded) {
    std::vector<std::byte> decoded;

    try {
        decoded = crypto::Base64::decode(encoded);
    } catch (const std::invalid_argument&) {
        // A character, a length, or padding that base64 does not have.
        return std::nullopt;
    }

    if (crypto::Base64::encode(decoded) != encoded) {
        return std::nullopt;
    }

    std::string text(decoded.size(), '\0');

    std::transform(decoded.begin(), decoded.end(), text.begin(),
                   [](std::byte byte) { return static_cast<char>(byte); });
    return text;
}

// user_pass, once it is known to join a username and password in UTF-8.
// Throws std::invalid_argument for one that does not.
inline std::string_view BasicCredentials::_checked(std::string_view user_pass) {
    if (std::string_view::npos == user_pass.find(':')) {
        throw std::invalid_argument("Basic credentials join a username and password with a ':'");
    }

    if (!_is_utf8(user_pass)) {
        throw std::invalid_argument("Basic credentials must be text in UTF-8");
    }

    return user_pass;
}

inline bool BasicCredentials::_is_utf8(std::string_view text) {
    while (!text.empty()) {
        const auto length = _utf8_length(text);

        if (0 == length) {
            return false;
        }

        text.remove_prefix(length);
    }

    return true;
}

// How many bytes the character in UTF-8 that text starts with takes, or 0 if
// it starts with none.
inline std::size_t BasicCredentials::_utf8_length(std::string_view text) {
    const auto first = static_cast<unsigned char>(text.at(0));
    const auto* const start = std::find_if(
        utf8_starts.begin(), utf8_starts.end(), [first](const Utf8Start& candidate) {
        return candidate.first_low <= first && first <= candidate.first_high;
    });

    if (utf8_starts.end() == start) {
        return 0;
    }

    return _has_rest_of(text, *start) ? start->length : 0;
}

// Whether text holds the bytes after the first of the character it starts
// with, which start describes.
inline bool BasicCredentials::_has_rest_of(std::string_view text, const Utf8Start& start) {
    if (text.size() < start.length) {
        return false;
    }

    for (std::size_t index = 1; index < start.length; ++index) {
        const auto byte = static_cast<unsigned char>(text.at(index));
        const auto low = 1 == index ? start.second_low : utf8_tail_low;
        const auto high = 1 == index ? start.second_high : utf8_tail_high;

        if (byte < low || high < byte) {
            return false;
        }
    }

    return true;
}

} // namespace http
