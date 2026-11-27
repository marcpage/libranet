#pragma once

#include <algorithm>
#include <cctype>
#include <cstddef>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "Headers.hpp"
#include "crypto/Base64.hpp"
#include "types/Converter.hpp"

namespace http {

// The username and password of the Basic scheme (RFC 7617 §2), in UTF-8. The
// password is wiped from memory as it goes.
class BasicCredentials {
public:
    static constexpr std::string_view field_name = "Authorization";

    // The credentials an Authorization field's value gives, whatever the case
    // of its scheme, or none when it names another scheme, or what it gives
    // is not base64, not UTF-8, or holds no ':'.
    [[nodiscard]] static std::optional<BasicCredentials> parse(std::string_view text);

    // Throws std::invalid_argument for user_pass with no ':', or not in UTF-8.
    explicit BasicCredentials(std::string_view user_pass);
    ~BasicCredentials();

    // Up to the first ':', which a username cannot hold (RFC 7617 §2).
    [[nodiscard]] const std::string& username() const noexcept;
    [[nodiscard]] const std::string& password() const noexcept;
    [[nodiscard]] std::string user_pass() const;
    // As an Authorization field's value.
    [[nodiscard]] std::string to_string() const;

    BasicCredentials(BasicCredentials&&) noexcept = default;
    BasicCredentials& operator=(BasicCredentials&&) noexcept = default;

    BasicCredentials(const BasicCredentials&) = delete;
    BasicCredentials& operator=(const BasicCredentials&) = delete;

private:
    // As RFC 7617 §2 writes it; it is read whatever its case (RFC 9110
    // §11.1).
    static constexpr std::string_view basic_scheme = "Basic";

    std::string m_username;
    std::string m_password;

    [[nodiscard]] static bool _is_basic(std::string_view scheme);
    [[nodiscard]] static std::optional<std::string> _decode(std::string_view encoded);
    [[nodiscard]] static std::string_view _checked(std::string_view user_pass);
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

    return types::Converter::text_of(decoded);
}

// user_pass, once it is known to join a username and password in UTF-8.
// Throws std::invalid_argument for one that does not.
inline std::string_view BasicCredentials::_checked(std::string_view user_pass) {
    if (std::string_view::npos == user_pass.find(':')) {
        throw std::invalid_argument("Basic credentials join a username and password with a ':'");
    }

    if (!types::Converter::is_utf8(user_pass)) {
        throw std::invalid_argument("Basic credentials must be text in UTF-8");
    }

    return user_pass;
}

} // namespace http
