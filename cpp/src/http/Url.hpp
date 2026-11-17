#pragma once

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <limits>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "Query.hpp"

namespace http {

class Url {
public:
    [[nodiscard]] static std::optional<Url> parse(std::string_view url_string);

    enum class EncodeContext : std::uint8_t {
        UriComponent, // RFC 3986: unreserved chars, spaces -> %20
        FormUrl,      // Query/Form: unreserved chars, spaces -> +
    };

    [[nodiscard]] static std::string encode(std::string_view value,
                                            EncodeContext context = EncodeContext::FormUrl);
    [[nodiscard]] static std::string encode(std::string_view value, std::string_view allowed_chars,
                                            bool space_to_plus);

    [[nodiscard]] static std::string decode(std::string_view value,
                                            EncodeContext context = EncodeContext::FormUrl);
    [[nodiscard]] static std::string decode(std::string_view value, bool decode_plus_as_space);

    Url() = default;
    ~Url() = default;
    Url(const Url&) = default;
    Url& operator=(const Url&) = default;
    Url(Url&&) noexcept = default;
    Url& operator=(Url&&) noexcept = default;

    [[nodiscard]] std::string_view scheme() const noexcept;
    [[nodiscard]] std::string username() const;
    [[nodiscard]] std::string password() const;
    [[nodiscard]] std::string_view host() const noexcept;
    [[nodiscard]] std::optional<uint16_t> port() const noexcept;
    [[nodiscard]] std::optional<uint16_t> effective_port() const;
    [[nodiscard]] std::string path() const;
    [[nodiscard]] std::string fragment() const;

    [[nodiscard]] const Query& query() const noexcept;
    Query& query() noexcept;
    [[nodiscard]] std::optional<std::string> get_query(std::string_view key) const;
    void set_query(std::string_view key, std::string_view value);
    [[nodiscard]] bool has_query(std::string_view key) const noexcept;
    void remove_query(std::string_view key);
    void set_query_parameters(
        std::initializer_list<std::pair<std::string_view, std::string_view>> parameters);

    void set_scheme(std::string new_scheme);
    void set_username(const std::string& new_username);
    void set_password(const std::string& new_password);
    void set_host(std::string new_host);
    void set_port(uint16_t new_port);
    void set_path(const std::string& new_path);
    void set_query(Query new_query);
    void set_fragment(const std::string& new_fragment);

    void append(std::string_view relative_url);

    [[nodiscard]] std::string to_string() const;

    friend bool operator==(const Url& left, const Url& right);
    friend bool operator!=(const Url& left, const Url& right);
    friend bool operator<(const Url& left, const Url& right);

    friend std::istream& operator>>(std::istream& in_stream, Url& url);
    friend std::ostream& operator<<(std::ostream& out_stream, const Url& url);

private:
    std::string m_scheme;
    std::string m_username;
    std::string m_password;
    std::string m_host;
    std::optional<uint16_t> m_port;
    std::string m_path = "/";
    Query m_query;
    std::string m_fragment;

    static void _extract_fragment(std::string_view& url_string, std::string& fragment);
    static void _extract_query(std::string_view& url_string, Query& query);
    static void _extract_scheme(std::string_view& url_string, std::string& scheme);
    static void _parse_authority(std::string_view authority, std::string& username,
                                 std::string& password, std::string& host,
                                 std::optional<uint16_t>& port);
    static std::string _to_lower_copy(std::string_view text);
    static std::optional<uint16_t> _parse_port(std::string_view text);
    [[nodiscard]] static bool _is_hex_digit(char character) noexcept;
};

inline std::string Url::encode(std::string_view value, std::string_view allowed_chars,
                               bool space_to_plus) {
    std::ostringstream escaped;

    // fill() returns the old fill character, which is not needed.
    (void)escaped.fill('0');
    escaped << std::hex;

    for (const auto character : value) {
        // Bytes above 0x7F are negative in a signed char, so they go through
        // unsigned char before std::isalnum and before printing as hex.
        const auto byte = static_cast<unsigned char>(character);

        if (' ' == character) {
            if (space_to_plus) {
                escaped << '+';
            } else {
                escaped << "%20";
            }
        } else if (std::isalnum(byte) != 0
                   || allowed_chars.find(character) != std::string_view::npos) {
            // Keep character as-is if it's alphanumeric or explicitly allowed
            escaped << character;
        } else {
            escaped << '%' << std::uppercase
                    << std::setw(2) << static_cast<int>(byte);
        }
    }

    return escaped.str();
}

inline std::string Url::encode(std::string_view value, EncodeContext context) {
    // RFC 3986 Section 2.3 Unreserved characters
    constexpr std::string_view unreserved = "-_.~";

    const bool space_to_plus = (EncodeContext::FormUrl == context);
    return encode(value, unreserved, space_to_plus);
}

// std::isxdigit takes an unsigned char's value.
inline bool Url::_is_hex_digit(char character) noexcept {
    return std::isxdigit(static_cast<unsigned char>(character)) != 0;
}

inline std::string Url::decode(std::string_view value, bool decode_plus_as_space) {
    std::string decoded;
    decoded.reserve(value.size());

    std::size_t character_index = 0;

    while (character_index < value.size()) {
        if (decode_plus_as_space && '+' == value.at(character_index)) {
            decoded += ' ';
            ++character_index;
        } else if ('%' == value.at(character_index) && character_index + 2 < value.size()
                   && _is_hex_digit(value.at(character_index + 1))
                   && _is_hex_digit(value.at(character_index + 2))) {
            constexpr int hex_base = 16;
            const std::string hex_digits(value.substr(character_index + 1, 2));

            decoded += static_cast<char>(std::stoi(hex_digits, nullptr, hex_base));
            character_index += 3;
        } else {
            decoded += value.at(character_index);
            ++character_index;
        }
    }

    return decoded;
}

inline std::string Url::decode(std::string_view value, EncodeContext context) {
    const bool decode_plus = (EncodeContext::FormUrl == context);
    return decode(value, decode_plus);
}

inline std::string_view Url::scheme() const noexcept {
    return m_scheme;
}

inline std::string Url::username() const {
    return decode(m_username, EncodeContext::UriComponent);
}

inline std::string Url::password() const {
    return decode(m_password, EncodeContext::UriComponent);
}

inline std::string_view Url::host() const noexcept {
    return m_host;
}

inline std::optional<std::uint16_t> Url::port() const noexcept {
    return m_port;
}

inline std::optional<uint16_t> Url::effective_port() const {
    const auto explicit_port = port();

    if (explicit_port.has_value()) {
        return explicit_port;
    }

    constexpr uint16_t http_port = 80;
    constexpr uint16_t https_port = 443;
    constexpr uint16_t ftp_port = 21;
    const std::string lower_scheme = _to_lower_copy(m_scheme);

    if ("http" == lower_scheme || "ws" == lower_scheme) {
        return http_port;
    }

    if ("https" == lower_scheme || "wss" == lower_scheme) {
        return https_port;
    }

    if ("ftp" == lower_scheme) {
        return ftp_port;
    }

    return std::nullopt; // Unknown or empty scheme/port
}

inline std::string Url::path() const {
    return decode(m_path, EncodeContext::UriComponent);
}

inline std::string Url::fragment() const {
    return decode(m_fragment, EncodeContext::UriComponent);
}

inline const Query& Url::query() const noexcept {
    return m_query;
}

inline Query& Url::query() noexcept {
    return m_query;
}

inline std::optional<std::string> Url::get_query(std::string_view key) const {
    const auto value = m_query.get_first(key);

    if (!value.has_value()) {
        return std::nullopt;
    }

    return decode(value.value(), EncodeContext::FormUrl);
}

inline void Url::set_query(std::string_view key, std::string_view value) {
    const std::string encoded_key = encode(key, EncodeContext::FormUrl);
    const std::string encoded_value = encode(value, EncodeContext::FormUrl);

    m_query.set(encoded_key, encoded_value);
}

inline void Url::set_query_parameters(
    std::initializer_list<std::pair<std::string_view, std::string_view>> parameters) {
    for (const auto& [key, value] : parameters) {
        set_query(key, value);
    }
}

inline bool Url::has_query(std::string_view key) const noexcept {
    const std::string encoded_key = encode(key, EncodeContext::FormUrl);
    return m_query.get_first(encoded_key).has_value();
}

inline void Url::remove_query(std::string_view key) {
    const std::string encoded_key = encode(key, EncodeContext::FormUrl);
    m_query.remove(encoded_key);
}

inline void Url::set_scheme(std::string new_scheme) {
    m_scheme = std::move(new_scheme);
}

inline void Url::set_username(const std::string& new_username) {
    constexpr std::string_view userinfo_allowed = "-_.~!$&'()*+,;=";
    constexpr auto not_space_to_plus = false;

    m_username = encode(new_username, userinfo_allowed, not_space_to_plus);
}

inline void Url::set_password(const std::string& new_password) {
    constexpr std::string_view userinfo_allowed = "-_.~!$&'()*+,;=";
    constexpr auto not_space_to_plus = false;

    m_password = encode(new_password, userinfo_allowed, not_space_to_plus);
}

inline void Url::set_host(std::string new_host) {
    if (new_host.size() >= 2 && '[' == new_host.front() && ']' == new_host.back()) {
        new_host.erase(0, 1);
        new_host.pop_back();
    }

    m_host = std::move(new_host);
}

inline void Url::set_port(uint16_t new_port) {
    m_port = new_port;
}

inline void Url::set_path(const std::string& new_path) {
    constexpr std::string_view unreserved_and_slash = "-_.~/";
    constexpr auto not_space_to_plus = false;

    m_path = encode(new_path, unreserved_and_slash, not_space_to_plus);
}

inline void Url::set_query(Query new_query) {
    m_query = std::move(new_query);
}

inline void Url::set_fragment(const std::string& new_fragment) {
    constexpr std::string_view fragment_allowed = "-_.~!$&'()*+,;=/?";
    constexpr auto not_space_to_plus = false;

    m_fragment = encode(new_fragment, fragment_allowed, not_space_to_plus);
}

inline std::optional<Url> Url::parse(std::string_view url_string) {
    if (url_string.empty()) {
        return std::nullopt;
    }

    Url url;

    _extract_fragment(url_string, url.m_fragment);
    _extract_query(url_string, url.m_query);
    _extract_scheme(url_string, url.m_scheme);

    // Separate Authority from Path
    const auto path_start = url_string.find('/');

    std::string_view authority;

    if (path_start != std::string_view::npos) {
        authority = url_string.substr(0, path_start);
        url.m_path = std::string(url_string.substr(path_start));
    } else {
        authority = url_string;
        url.m_path = "/";
    }

    _parse_authority(authority, url.m_username, url.m_password, url.m_host, url.m_port);

    // Only the brackets around an IPv6 address: in a host they would be
    // written as if they were those.
    if (url.m_host.find_first_of("[]") != std::string::npos) {
        return std::nullopt;
    }

    return url;
}

inline std::string Url::to_string() const {
    std::string result;

    if (!m_scheme.empty()) {
        result += m_scheme;
        result += "://";
    }

    if (!m_username.empty() || !m_password.empty()) {
        result += m_username;

        if (!m_password.empty()) {
            result += ":" + m_password;
        }

        result += '@';
    }

    const bool is_ipv6 = (m_host.find(':') != std::string::npos);

    if (is_ipv6 && (m_host.front() != '[' || m_host.back() != ']')) {
        result += "[" + m_host + "]";
    } else {
        result += m_host;
    }

    if (m_port.has_value()) {
        result += ':';
        result += std::to_string(m_port.value());
    }

    result += m_path;

    if (!m_query.empty()) {
        result += '?';
        result += m_query.to_string();
    }

    if (!m_fragment.empty()) {
        result += '#';
        result += m_fragment;
    }

    return result;
}


inline std::istream& operator>>(std::istream& in_stream, Url& url) {
    std::string url_text;

    if (!(in_stream >> url_text)) {
        return in_stream;
    }

    const auto parsed_url = Url::parse(url_text);

    if (!parsed_url.has_value()) {
        in_stream.setstate(std::ios_base::failbit);
        return in_stream;
    }

    url = parsed_url.value();

    return in_stream;
}

inline std::ostream& operator<<(std::ostream& out_stream, const Url& url) {
    out_stream << url.to_string();
    return out_stream;
}

inline void Url::_extract_fragment(std::string_view& url_string, std::string& fragment) {
    const auto fragment_position = url_string.find('#');

    if (fragment_position != std::string_view::npos) {
        fragment = std::string(url_string.substr(fragment_position + 1));
        url_string = url_string.substr(0, fragment_position);
    }
}

inline void Url::_extract_query(std::string_view& url_string, Query& query) {
    const auto query_position = url_string.find('?');

    if (query_position != std::string_view::npos) {
        // With its '?', which Query::parse drops, so that a query that
        // itself starts with '?' keeps it.
        query = Query::parse(url_string.substr(query_position));
        url_string = url_string.substr(0, query_position);
    }
}

inline void Url::_extract_scheme(std::string_view& url_string, std::string& scheme) {
    const auto scheme_end = url_string.find("://");

    // A "://" after the first '/' is in the path, and names no scheme.
    if (scheme_end != std::string_view::npos && scheme_end < url_string.find('/')) {
        scheme = std::string(url_string.substr(0, scheme_end));
        url_string.remove_prefix(scheme_end + 3);
    }
}

inline void Url::_parse_authority(std::string_view authority, std::string& username,
                                  std::string& password, std::string& host,
                                  std::optional<uint16_t>& port) {
    if (authority.empty()) {
        return;
    }

    const auto at_position = authority.rfind('@');

    if (at_position != std::string_view::npos) {
        const auto user_info = authority.substr(0, at_position);
        const auto colon_position = user_info.find(':');

        if (colon_position != std::string_view::npos) {
            username = std::string(user_info.substr(0, colon_position));
            password = std::string(user_info.substr(colon_position + 1));
        } else {
            username = std::string(user_info);
        }

        authority.remove_prefix(at_position + 1);
    }

    const auto colon_position = authority.rfind(':');

    if (colon_position != std::string_view::npos) {
        const auto closing_bracket = authority.rfind(']');

        if (std::string_view::npos == closing_bracket || colon_position > closing_bracket) {
            const auto port_string = std::string(authority.substr(colon_position + 1));

            port = _parse_port(port_string);
            authority = authority.substr(0, colon_position);
        }
    }

    if (authority.size() >= 2 && '[' == authority.front() && ']' == authority.back()) {
        authority.remove_prefix(1);
        authority.remove_suffix(1);
    }

    host = std::string(authority);
}

inline void Url::append(std::string_view relative_url) {
    if (relative_url.empty()) {
        return;
    }

    // 1. If it's an absolute URL (contains a scheme), parse and replace entirely
    if (relative_url.find("://") != std::string_view::npos) {
        auto parsed = Url::parse(relative_url);

        if (parsed.has_value()) {
            *this = std::move(parsed.value());
        }

        return;
    }

    // 2. Extract fragment if present in the relative reference
    std::string rel_fragment;

    _extract_fragment(relative_url, rel_fragment);

    if (!rel_fragment.empty()) {
        m_fragment = std::move(rel_fragment);
    }

    // 3. Extract query if present in the relative reference
    Query rel_query;

    _extract_query(relative_url, rel_query);

    if (!rel_query.empty()) {
        m_query = std::move(rel_query);
    }

    if (relative_url.empty()) {
        return;
    }

    // 4. If relative path starts with '/', it replaces the path entirely
    if ('/' == relative_url.front()) {
        m_path = std::string(relative_url);
        return;
    }

    // 5. Otherwise, resolve relative path against the current m_path directory
    std::string new_path = m_path;
    const auto last_slash = new_path.rfind('/');

    if (last_slash != std::string::npos) {
        new_path.erase(last_slash + 1); // Retain up to and including the last slash
    } else {
        new_path = "/";
    }

    new_path += relative_url;
    m_path = std::move(new_path);
}

inline std::string Url::_to_lower_copy(std::string_view text) {
    std::string result(text);

    std::transform(result.begin(), result.end(), result.begin(), [](char character) {
        return static_cast<char>(std::tolower(static_cast<unsigned char>(character)));
    });

    return result;
}

inline bool operator==(const Url& left, const Url& right) {
    return Url::_to_lower_copy(left.m_scheme) == Url::_to_lower_copy(right.m_scheme)
           && left.m_username == right.m_username
           && left.m_password == right.m_password
           && Url::_to_lower_copy(left.m_host) == Url::_to_lower_copy(right.m_host)
           && left.m_port == right.m_port
           && left.m_path == right.m_path
           && left.m_query.to_string() == right.m_query.to_string()
           && left.m_fragment == right.m_fragment;
}

inline bool operator!=(const Url& left, const Url& right) {
    return !(left == right);
}

inline bool operator<(const Url& left, const Url& right) {
    const auto left_scheme = Url::_to_lower_copy(left.m_scheme);
    const auto right_scheme = Url::_to_lower_copy(right.m_scheme);

    if (left_scheme != right_scheme) {
        return left_scheme < right_scheme;
    }

    if (left.m_username != right.m_username) {
        return left.m_username < right.m_username;
    }

    if (left.m_password != right.m_password) {
        return left.m_password < right.m_password;
    }

    const auto left_host = Url::_to_lower_copy(left.m_host);
    const auto right_host = Url::_to_lower_copy(right.m_host);

    if (left_host != right_host) {
        return left_host < right_host;
    }

    if (left.m_port != right.m_port) {
        return left.m_port < right.m_port;
    }

    if (left.m_path != right.m_path) {
        return left.m_path < right.m_path;
    }

    const auto left_query = left.m_query.to_string();
    const auto right_query = right.m_query.to_string();

    if (left_query != right_query) {
        return left_query < right_query;
    }

    return left.m_fragment < right.m_fragment;
}

inline std::optional<uint16_t> Url::_parse_port(std::string_view text) {
    constexpr auto max_port_number = std::numeric_limits<uint16_t>::max();

    if (text.empty()) {
        return std::nullopt;
    }

    try {
        const auto port_number = std::stoul(std::string(text));

        if (port_number <= max_port_number) {
            return static_cast<uint16_t>(port_number);
        }
    } catch (const std::invalid_argument&) {
        return std::nullopt; // not a number
    } catch (const std::out_of_range&) {
        return std::nullopt; // too large for unsigned long
    }

    return std::nullopt;
}

} // namespace http
