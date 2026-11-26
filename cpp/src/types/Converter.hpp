#pragma once

#include <algorithm>
#include <array>
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <numeric>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "Constants.hpp"

namespace types {

class Converter {
public:
    using Bytes = std::vector<std::byte>;
    static std::string lower_case(std::string_view text);
    static Bytes bytes_of(std::string_view text);
    static std::string text_of(const Converter::Bytes& bytes);
    static std::uint64_t hex_digit_value(char hex_digit);
    static std::uint64_t parse_hex(std::string_view hex_text);
    // text without the whitespace around it.
    [[nodiscard]] static std::string_view trimmed(std::string_view text);
    // Whether text is in UTF-8 (RFC 3629).
    [[nodiscard]] static bool is_utf8(std::string_view text);

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

    [[nodiscard]] static std::size_t _utf8_length(std::string_view text);
    [[nodiscard]] static bool _has_rest_of(std::string_view text, const Utf8Start& start);
};

inline std::string Converter::lower_case(std::string_view text) {
    std::string lower(text);

    std::transform(lower.begin(), lower.end(), lower.begin(), [](char character) {
        return static_cast<char>(std::tolower(static_cast<unsigned char>(character)));
    });
    return lower;
}

inline Converter::Bytes Converter::bytes_of(std::string_view text) {
    Bytes bytes(text.size());

    std::transform(text.begin(), text.end(), bytes.begin(),
                   [](char character) { return static_cast<std::byte>(character); });
    return bytes;
}

inline std::string Converter::text_of(const Converter::Bytes& bytes) {
    std::string text(bytes.size(), '\0');

    std::transform(bytes.begin(), bytes.end(), text.begin(),
                   [](std::byte byte) { return static_cast<char>(byte); });
    return text;
}

inline std::uint64_t Converter::hex_digit_value(char hex_digit) {
    return static_cast<std::uint64_t>(hex_digits.find(hex_digit));
}

inline std::uint64_t Converter::parse_hex(std::string_view hex_text) {
    constexpr auto max_hex_digits = 16;

    if (hex_text.size() > max_hex_digits) {
        throw std::invalid_argument(std::string("Too many digits: "
                                                + std::string(hex_text)
                                                + " ( > " + std::to_string(max_hex_digits) + ")"));
    }

    const auto append_digit = [](std::uint64_t read, char digit) {
        return (read << bits_per_hex_digit) | Converter::hex_digit_value(digit);
    };

    return std::accumulate(hex_text.begin(), hex_text.end(), std::uint64_t{0}, append_digit);
}

inline std::string_view Converter::trimmed(std::string_view text) {
    constexpr std::string_view whitespace = " \t\n\v\f\r";
    const auto first = text.find_first_not_of(whitespace);

    if (std::string_view::npos == first) {
        return {};
    }

    return text.substr(first, text.find_last_not_of(whitespace) - first + 1);
}

inline bool Converter::is_utf8(std::string_view text) {
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
inline std::size_t Converter::_utf8_length(std::string_view text) {
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
inline bool Converter::_has_rest_of(std::string_view text, const Utf8Start& start) {
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

} // namespace types
