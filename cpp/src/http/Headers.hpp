#pragma once

#include <algorithm>
#include <cctype>
#include <iostream>
#include <map>
#include <optional>
#include <sstream>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace http {

class Headers {
public:
    struct CaseInsensitiveLess {
        using is_transparent = void;

        bool operator()(std::string_view lhs, std::string_view rhs) const noexcept;
    };

    Headers() = default;
    Headers(const Headers&) = default;
    Headers(Headers&&) noexcept = default;
    Headers& operator=(const Headers&) = default;
    Headers& operator=(Headers&&) noexcept = default;
    ~Headers() = default;

    void set(std::string key, std::string value);
    [[nodiscard]] std::optional<std::string_view> get(std::string_view key) const noexcept;
    [[nodiscard]] std::string_view get(std::string_view key,
                                       std::string_view default_value) const noexcept;
    [[nodiscard]] bool has(std::string_view key) const noexcept;
    void remove(const std::string& key);
    void clear() noexcept;

    [[nodiscard]] std::size_t size() const noexcept;
    [[nodiscard]] bool empty() const noexcept;

    [[nodiscard]] std::string to_string() const;

    friend bool operator==(const Headers& left, const Headers& right) noexcept;
    friend bool operator!=(const Headers& left, const Headers& right) noexcept;

    friend std::ostream& operator<<(std::ostream& out_stream, const Headers& headers);
    friend std::istream& operator>>(std::istream& in_stream, Headers& headers);

    static std::string_view trim_trailing_cr(std::string_view line) noexcept;
    static std::string_view trim_whitespace(std::string_view text);
    static bool is_continuation_line(std::string_view line) noexcept;
    static bool parse_header_line(std::string_view line, std::string& out_key,
                                  std::string& out_value);
    static std::string process_header_line(std::string_view line,
                                           const std::string& last_key, Headers& headers);
    static std::vector<std::string_view> split_spaces(
        std::string_view text, std::size_t max_fields);

private:
    std::map<std::string, std::string, CaseInsensitiveLess> m_headers;
};

inline bool Headers::CaseInsensitiveLess::operator()(std::string_view lhs,
                                                     std::string_view rhs) const noexcept {
    return std::lexicographical_compare(
        lhs.begin(), lhs.end(),
        rhs.begin(), rhs.end(),
        [](unsigned char left_char, unsigned char right_char) {
        return std::tolower(left_char) < std::tolower(right_char);
    });
}

// Inline implementations for Headers member functions
inline void Headers::set(std::string key, std::string value) {
    m_headers[std::move(key)] = std::move(value);
}

inline std::optional<std::string_view> Headers::get(std::string_view key) const noexcept {
    const auto header = m_headers.find(key);

    if (header != m_headers.end()) {
        return header->second;
    }

    return std::nullopt;
}

inline std::string_view Headers::get(std::string_view key, std::string_view default_value) const noexcept {
    const auto header = m_headers.find(key);

    if (header != m_headers.end()) {
        return header->second;
    }

    return default_value;
}

inline bool Headers::has(std::string_view key) const noexcept {
    return m_headers.find(key) != m_headers.end();
}

inline void Headers::remove(const std::string& key) {
    m_headers.erase(key);
}

inline void Headers::clear() noexcept {
    m_headers.clear();
}

inline std::size_t Headers::size() const noexcept {
    return m_headers.size();
}

inline bool Headers::empty() const noexcept {
    return m_headers.empty();
}

inline std::string Headers::to_string() const {
    constexpr auto AverageHeaderLineSize = 32;
    const auto* HttpHeaderNewline = "\r\n";
    std::string result;
    result.reserve(m_headers.size() * AverageHeaderLineSize);

    for (const auto& [key, value] : m_headers) {
        result += key;
        result += ": ";
        result += value;
        result += HttpHeaderNewline;
    }

    result += HttpHeaderNewline;
    return result;
}

inline bool operator==(const Headers& left, const Headers& right) noexcept {
    if (left.size() != right.size()) {
        return false;
    }

    // Use our case-insensitive comparator to check keys and values
    const Headers::CaseInsensitiveLess comp;

    // Since std::map is sorted by CaseInsensitiveLess, if two maps have the
    // same size and equal elements according to the comparator, we can iterate
    // and verify keys/values match case-insensitively.
    auto it1 = left.m_headers.begin();
    auto it2 = right.m_headers.begin();

    while (it1 != left.m_headers.end() && it2 != right.m_headers.end()) {
        // Keys must be equivalent case-insensitively (neither is less than the other)
        if (comp(it1->first, it2->first) || comp(it2->first, it1->first)) {
            return false;
        }
        // Values must match exactly
        if (it1->second != it2->second) {
            return false;
        }
        ++it1;
        ++it2;
    }

    return true;
}

inline bool operator!=(const Headers& left, const Headers& right) noexcept {
    return !(left == right);
}

inline std::ostream& operator<<(std::ostream& out_stream, const Headers& headers) {
    return out_stream << headers.to_string();
}

inline std::istream& operator>>(std::istream& in_stream, Headers& headers) {
    std::string raw_line;
    std::string last_key;

    headers.clear();

    while (std::getline(in_stream, raw_line)) {
        const std::string_view line = Headers::trim_trailing_cr(raw_line);

        if (line.empty()) {
            break; // Successfully finished headers
        }

        last_key = Headers::process_header_line(line, last_key, headers);
    }

    return in_stream;
}

inline std::string Headers::process_header_line(
    std::string_view line,
    const std::string& last_key,
    Headers& headers) {
    if (line.empty()) {
        return {};
    }

    if (is_continuation_line(line)) {
        if (last_key.empty()) {
            throw std::runtime_error("Header starts with continuation line: "
                                     + std::string(line));
        }

        const auto existing_val = headers.get(last_key);

        if (!existing_val.has_value()) {
            throw std::invalid_argument("Key does not exist: " + std::string(last_key));
        }

        std::string updated_value(existing_val.value());
        const std::string_view cleaned_chunk = trim_whitespace(line);

        updated_value.append(" ");
        updated_value.append(cleaned_chunk.data(), cleaned_chunk.size());
        headers.set(last_key, std::move(updated_value));
        return last_key;
    }

    std::string new_key;
    std::string new_value;

    if (!parse_header_line(line, new_key, new_value)) {
        throw std::runtime_error("Missing colon in header line: " + std::string(line));
    }

    headers.set(new_key, std::move(new_value));
    return new_key;
}

inline std::string_view Headers::trim_trailing_cr(std::string_view line) noexcept {
    if (!line.empty() && line.back() == '\r') {
        line.remove_suffix(1);
    }

    return line;
}

inline std::string_view Headers::trim_whitespace(std::string_view text) {
    const auto start = text.find_first_not_of(" \t\r\n");

    if (start == std::string_view::npos) {
        return {};
    }

    text = text.substr(start);
    const auto end = text.find_last_not_of(" \t\r\n");

    return text.substr(0, end + 1);
}

inline bool Headers::is_continuation_line(std::string_view line) noexcept {
    return !line.empty() && (line[0] == ' ' || line[0] == '\t');
}

inline bool Headers::parse_header_line(std::string_view line, std::string& out_key, std::string& out_value) {
    const auto colon_pos = line.find(':');

    if (colon_pos == std::string_view::npos) {
        return false;
    }

    out_key = std::string(line.substr(0, colon_pos));
    out_value = std::string(trim_whitespace(line.substr(colon_pos + 1)));

    return true;
}

// Add inside class Headers (or as a static helper):
inline std::vector<std::string_view> Headers::split_spaces(std::string_view text, std::size_t max_fields) {
    std::vector<std::string_view> fields;
    if (text.empty() || max_fields == 0) {
        return fields;
    }

    std::size_t pos = 0;
    while (fields.size() + 1 < max_fields && pos < text.size()) {
        pos = text.find_first_not_of(" \t", pos);
        if (pos == std::string_view::npos) {
            break;
        }
        const auto end = text.find_first_of(" \t", pos);
        if (end == std::string_view::npos) {
            fields.push_back(text.substr(pos));
            pos = std::string_view::npos;
            break;
        }
        fields.push_back(text.substr(pos, end - pos));
        pos = end;
    }

    // If there is remaining text and we need the final catch-all field
    if (pos < text.size() && fields.size() == max_fields - 1) {
        pos = text.find_first_not_of(" \t", pos);
        if (pos != std::string_view::npos) {
            fields.push_back(text.substr(pos));
        }
    }

    return fields;
}
} // namespace http
