#pragma once

#include <algorithm>
#include <cctype>
#include <numeric>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace types {

class Converter {
public:
    using Bytes = std::vector<std::byte>;
    static std::string lower_case(std::string_view text);
    static Bytes bytes_of(std::string_view text);
    static std::string text_of(const Converter::Bytes& bytes);
    static uint64_t hex_digit_value(char hex_digit);
    static uint64_t parse_hex(std::string_view hex_text);
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

inline uint64_t Converter::hex_digit_value(char hex_digit) {
    constexpr std::string_view digits = "0123456789abcdef";

    return static_cast<uint64_t>(digits.find(hex_digit));
}

inline uint64_t Converter::parse_hex(std::string_view hex_text) {
    constexpr auto max_hex_digits = 16;
    constexpr auto bits_per_digit = 4U;

    if (hex_text.size() > max_hex_digits) {
        throw std::invalid_argument(std::string("Too many digits: "
                                                + std::string(hex_text)
                                                + " ( > " + std::to_string(max_hex_digits) + ")"));
    }

    return std::accumulate(hex_text.begin(), hex_text.end(), uint64_t{0}, [](uint64_t read, char digit) {
        return (read << bits_per_digit) | Converter::hex_digit_value(digit);
    });
}

} // namespace types
