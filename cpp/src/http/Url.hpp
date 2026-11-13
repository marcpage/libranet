#pragma once

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
    static std::optional<Url> parse(std::string_view url_string);

    enum class EncodeContext {
        UriComponent, // RFC 3986: unreserved chars, spaces -> %20
        FormUrl,       // Query/Form: unreserved chars, spaces -> +
    };

    static std::string encode(std::string_view value,
                                            EncodeContext context=EncodeContext::FormUrl);
    static std::string encode(std::string_view value, std::string_view allowed_chars,
                                                                    bool space_to_plus);

    static std::string decode(std::string_view value,
                                            EncodeContext context=EncodeContext::FormUrl);
    static std::string decode(std::string_view value, bool decode_plus_as_space);

    Url() = default;
    ~Url() = default;
    Url(const Url&) = default;
    Url& operator=(const Url&) = default;
    Url(Url&&) noexcept = default;
    Url& operator=(Url&&) noexcept = default;

    std::string_view scheme() const noexcept;
    std::string username() const noexcept;
    std::string password() const noexcept;
    std::string_view host() const noexcept;
    std::optional<uint16_t> port() const noexcept;
    std::optional<uint16_t> effective_port() const noexcept;
    std::string path() const;
    std::string fragment() const noexcept;

    const Query& query() const noexcept;
    Query& query() noexcept;
    std::optional<std::string> get_query(std::string_view key) const;
    void set_query(std::string_view key, std::string_view value);
    bool has_query(std::string_view key) const noexcept;
    void remove_query(std::string_view key);
    void set_query_parameters(
        std::initializer_list<std::pair<std::string_view, std::string_view>> parameters);

    void set_scheme(std::string scheme);
    void set_username(std::string username);
    void set_password(std::string password);
    void set_host(std::string host);
    void set_port(uint16_t port);
    void set_path(std::string path);
    void set_query(Query query);
    void set_fragment(std::string fragment);

    void append(std::string_view relative_url);

    std::string to_string() const;

    friend bool operator==(const Url& lhs, const Url& rhs);
    friend bool operator!=(const Url& lhs, const Url& rhs);
    friend bool operator<(const Url& lhs, const Url& rhs);

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

    static void extract_fragment(std::string_view& url_string, std::string& fragment);
    static void extract_query(std::string_view& url_string, Query& query);
    static void extract_scheme(std::string_view& url_string, std::string& scheme);
    static void parse_authority(std::string_view authority, std::string& username,
                std::string& password, std::string& host, std::optional<uint16_t>& port);
    static std::string to_lower_copy(std::string_view str);
    static std::optional<uint16_t> parse_port(std::string_view str);
};

inline std::string Url::encode(std::string_view value, std::string_view allowed_chars,
                                                                    bool space_to_plus) {
    std::ostringstream escaped;

    escaped.fill('0');
    escaped << std::hex;

    for (const auto character : value) {
        if (character == ' ') {
            if (space_to_plus) {
                escaped << '+';
            } else {
                escaped << "%20";
            }
        } else if (std::isalnum(character)
                    || allowed_chars.find(character) != std::string_view::npos) {
            // Keep character as-is if it's alphanumeric or explicitly allowed
            escaped << character;
        } else {
            escaped << '%' << std::uppercase
                    << std::setw(2) << static_cast<int>(character);
        }
    }

    return escaped.str();
}

inline std::string Url::encode(std::string_view value, EncodeContext context) {
    // RFC 3986 Section 2.3 Unreserved characters
    constexpr std::string_view unreserved = "-_.~";

    bool space_to_plus = (context == EncodeContext::FormUrl);
    return encode(value, unreserved, space_to_plus);
}

inline std::string Url::decode(std::string_view value, bool decode_plus_as_space) {
    std::string decoded;
    decoded.reserve(value.size());

    for (std::size_t character_index = 0; character_index < value.size();
                                                                    ++character_index) {
        if (decode_plus_as_space && value[character_index] == '+') {
            decoded += ' ';
        } else if (value[character_index] == '%' && character_index + 2 < value.size()) {
            int hex_value = 0;
            std::istringstream hex_stream(std::string(
                                                value.substr(character_index + 1, 2)));

            if (hex_stream >> std::hex >> hex_value) {
                decoded += static_cast<char>(hex_value);
                character_index += 2;
            } else {
                decoded += value[character_index];
            }
        } else {
            decoded += value[character_index];
        }
    }

    return decoded;
}

inline std::string Url::decode(std::string_view value, EncodeContext context) {
    bool decode_plus = (context == EncodeContext::FormUrl);
    return decode(value, decode_plus);
}

inline std::string_view Url::scheme() const noexcept {
    return m_scheme;
}

inline std::string Url::username() const noexcept {
    return decode(m_username, EncodeContext::UriComponent);
}

inline std::string Url::password() const noexcept {
    return decode(m_password, EncodeContext::UriComponent);
}

inline std::string_view Url::host() const noexcept {
    return m_host;
}

inline std::optional<std::uint16_t> Url::port() const noexcept {
    return m_port;
}

inline std::optional<uint16_t> Url::effective_port() const noexcept {
    const auto explicit_port = port();

    if (explicit_port.has_value()) {
        return explicit_port;
    }

    const std::string lower_scheme = to_lower_copy(m_scheme);

    if (lower_scheme == "http" || lower_scheme == "ws") {
        return 80;
    }

    if (lower_scheme == "https" || lower_scheme == "wss") {
        return 443;
    }

    if (lower_scheme == "ftp") {
        return 21;
    }

    return std::nullopt; // Unknown or empty scheme/port
}

inline std::string Url::path() const {
    return decode(m_path, EncodeContext::UriComponent);
}

inline std::string Url::fragment() const noexcept {
    return decode(m_fragment, EncodeContext::UriComponent);
}

inline const Query& Url::query() const noexcept {
    return m_query;
}

inline Query& Url::query() noexcept {
    return m_query;
}

inline std::optional<std::string> Url::get_query(std::string_view key) const {
    const auto value = m_query.get_first(std::string(key));

    if (!value.has_value()) {
        return std::nullopt;
    }
    return decode(value.value(), EncodeContext::FormUrl);
}

inline void Url::set_query(std::string_view key, std::string_view value) {
    const std::string encoded_key = encode(key, EncodeContext::FormUrl);
    const std::string encoded_val = encode(value, EncodeContext::FormUrl);

    m_query.set(encoded_key, encoded_val);
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

inline void Url::set_scheme(std::string scheme) {
    m_scheme = std::move(scheme);
}

inline void Url::set_username(std::string username) {
    constexpr std::string_view userinfo_allowed = "-_.~!$&'()*+,;=";
    constexpr auto not_space_to_plus = false;

    m_username = encode(username, userinfo_allowed, not_space_to_plus);
}

inline void Url::set_password(std::string password) {
    constexpr std::string_view userinfo_allowed = "-_.~!$&'()*+,;=";
    constexpr auto not_space_to_plus = false;

    m_password = encode(password, userinfo_allowed, not_space_to_plus);
}

inline void Url::set_host(std::string host) {
    if (host.size() >= 2 && host.front() == '[' && host.back() == ']') {
        host.erase(0, 1);
        host.pop_back();
    }

    m_host = std::move(host);
}

inline void Url::set_port(uint16_t port) {
    m_port = port;
}

inline void Url::set_path(std::string path) {
    constexpr std::string_view unreserved_and_slash = "-_.~/";
    constexpr auto not_space_to_plus = false;

    m_path = encode(path, unreserved_and_slash, not_space_to_plus);
}

inline void Url::set_query(Query query) {
    m_query = std::move(query);
}

inline void Url::set_fragment(std::string fragment) {
    constexpr std::string_view fragment_allowed = "-_.~!$&'()*+,;=/?";
    constexpr auto not_space_to_plus = false;

    m_fragment = encode(fragment, fragment_allowed, not_space_to_plus);
}

inline std::optional<Url> Url::parse(std::string_view url_string) {
    if (url_string.empty()) {
        return std::nullopt;
    }

    Url url;

    extract_fragment(url_string, url.m_fragment);
    extract_query(url_string, url.m_query);
    extract_scheme(url_string, url.m_scheme);

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

    parse_authority(authority, url.m_username, url.m_password, url.m_host, url.m_port);

    return url;
}

inline std::string Url::to_string() const {
    std::string result;

    if (!m_scheme.empty()) {
        result += m_scheme;
        result += "://";
    }

    if (!m_username.empty()) {
        result += m_username;

        if (!m_password.empty()) {
            result += ":" + m_password;
        }

        result += "@";
    }

    const bool is_ipv6 = (m_host.find(':') != std::string::npos);

    if (is_ipv6 && (m_host.front() != '[' || m_host.back() != ']')) {
        result += "[" + m_host + "]";
    } else {
        result += m_host;
    }

    if (m_port.has_value()) {
        result += ":";
        result += std::to_string(m_port.value());
    }

    result += m_path;

    if (!m_query.empty()) {
        result += "?";
        result += m_query.to_string();
    }

    if (!m_fragment.empty()) {
        result += "#";
        result += m_fragment;
    }

    return result;
}


inline std::istream& operator>>(std::istream& in_stream, Url& url) {
    std::string url_str;

    if (!(in_stream >> url_str)) {
        return in_stream;
    }

    const auto parsed_url = Url::parse(url_str);

    if (!parsed_url.has_value()) {
        in_stream.setstate(std::ios_base::failbit);
        return in_stream;
    }

    url = std::move(parsed_url.value());

    return in_stream;
}

inline std::ostream& operator<<(std::ostream& out_stream, const Url& url) {
    out_stream << url.to_string();
    return out_stream;
}

inline void Url::extract_fragment(std::string_view& url_string, std::string& fragment) {
    const auto frag_pos = url_string.find('#');

    if (frag_pos != std::string_view::npos) {
        fragment = std::string(url_string.substr(frag_pos + 1));
        url_string = url_string.substr(0, frag_pos);
    }
}

inline void Url::extract_query(std::string_view& url_string, Query& query) {
    const auto query_pos = url_string.find('?');

    if (query_pos != std::string_view::npos) {
        query = Query::parse(url_string.substr(query_pos + 1));
        url_string = url_string.substr(0, query_pos);
    }
}

inline void Url::extract_scheme(std::string_view& url_string, std::string& scheme) {
    const auto scheme_end = url_string.find("://");

    if (scheme_end != std::string_view::npos) {
        scheme = std::string(url_string.substr(0, scheme_end));
        url_string.remove_prefix(scheme_end + 3);
    }
}

inline void Url::parse_authority(std::string_view authority, std::string& username,
        std::string& password, std::string& host, std::optional<uint16_t>& port) {
    if (authority.empty()) {
        return;
    }

    const auto at_pos = authority.rfind('@');

    if (at_pos != std::string_view::npos) {
        const auto user_info_str = authority.substr(0, at_pos);
        const auto colon_pos = user_info_str.find(':');

        if (colon_pos != std::string_view::npos) {
            username = std::string(user_info_str.substr(0, colon_pos));
            password = std::string(user_info_str.substr(colon_pos + 1));
        } else {
            username = std::string(user_info_str);
        }

        authority.remove_prefix(at_pos + 1);
    }

    const auto colon_pos = authority.rfind(':');

    if (colon_pos != std::string_view::npos) {
        const auto closing_bracket = authority.rfind(']');

        if (closing_bracket == std::string_view::npos || colon_pos > closing_bracket) {
            const auto port_string = std::string(authority.substr(colon_pos + 1));

            port = parse_port(port_string);
            authority = authority.substr(0, colon_pos);
        }
    }

    if (authority.size() >= 2 && authority.front() == '[' && authority.back() == ']') {
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

    extract_fragment(relative_url, rel_fragment);

    if (!rel_fragment.empty()) {
        m_fragment = std::move(rel_fragment);
    }

    // 3. Extract query if present in the relative reference
    Query rel_query;

    extract_query(relative_url, rel_query);

    if (!rel_query.empty()) {
        m_query = std::move(rel_query);
    }

    if (relative_url.empty()) {
        return;
    }

    // 4. If relative path starts with '/', it replaces the path entirely
    if (relative_url.front() == '/') {
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

inline std::string Url::to_lower_copy(std::string_view str) {
    std::string result(str);

    for (auto& character : result) {
        character = static_cast<char>(std::tolower(static_cast<unsigned char>(character)));
    }

    return result;
}

inline bool operator==(const Url& lhs, const Url& rhs) {
    return Url::to_lower_copy(lhs.m_scheme) == Url::to_lower_copy(rhs.m_scheme) &&
           lhs.m_username == rhs.m_username &&
           lhs.m_password == rhs.m_password &&
           Url::to_lower_copy(lhs.m_host) == Url::to_lower_copy(rhs.m_host) &&
           lhs.m_port == rhs.m_port &&
           lhs.m_path == rhs.m_path &&
           lhs.m_query.to_string() == rhs.m_query.to_string() &&
           lhs.m_fragment == rhs.m_fragment;
}

inline bool operator!=(const Url& lhs, const Url& rhs) {
    return !(lhs == rhs);
}

inline bool operator<(const Url& lhs, const Url& rhs) {
    const auto lhs_scheme = Url::to_lower_copy(lhs.m_scheme);
    const auto rhs_scheme = Url::to_lower_copy(rhs.m_scheme);

    if (lhs_scheme != rhs_scheme) {
        return lhs_scheme < rhs_scheme;
    }

    if (lhs.m_username != rhs.m_username) {
        return lhs.m_username < rhs.m_username;
    }

    if (lhs.m_password != rhs.m_password) {
        return lhs.m_password < rhs.m_password;
    }

    const auto lhs_host = Url::to_lower_copy(lhs.m_host);
    const auto rhs_host = Url::to_lower_copy(rhs.m_host);

    if (lhs_host != rhs_host) {
        return lhs_host < rhs_host;
    }

    if (lhs.m_port != rhs.m_port) {
        return lhs.m_port < rhs.m_port;
    }

    if (lhs.m_path != rhs.m_path) {
        return lhs.m_path < rhs.m_path;
    }

    const auto lhs_query = lhs.m_query.to_string();
    const auto rhs_query = rhs.m_query.to_string();

    if (lhs_query != rhs_query) {
        return lhs_query < rhs_query;
    }

    return lhs.m_fragment < rhs.m_fragment;
}

inline std::optional<uint16_t> Url::parse_port(std::string_view str) {
    constexpr auto MaxPortNumber = std::numeric_limits<uint16_t>::max();

    if (str.empty()) {
        return std::nullopt;
    }

    try {
        unsigned long p = std::stoul(std::string(str));

        if (p <= MaxPortNumber) {
            return static_cast<uint16_t>(p);
        }
    } catch (const std::invalid_argument &) {
        // Fallthrough on conversion error
    } catch (const std::out_of_range &) {
        // Fallthrough on conversion error
    }

    return std::nullopt;
}

} // namespace http
