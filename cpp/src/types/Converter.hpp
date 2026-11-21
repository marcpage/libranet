#pragma once

#include <algorithm>
#include <cctype>
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

} // namespace types
