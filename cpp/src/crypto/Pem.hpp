#pragma once

#include <algorithm>
#include <cstddef>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "Base64.hpp"

namespace crypto {

class Pem {
public:
    struct Unwrapped {
        std::string label;
        std::vector<std::byte> der_bytes;
    };

    [[nodiscard]] static std::string wrap(std::string_view label,
                                          const std::vector<std::byte>& der_bytes);
    [[nodiscard]] static std::string wrap(std::string_view label, std::string_view der_string);
    [[nodiscard]] static Unwrapped unwrap(std::string_view pem_string);

    Pem() = delete;
    ~Pem() = delete;
    Pem(const Pem&) = delete;
    Pem& operator=(const Pem&) = delete;
    Pem(Pem&&) = delete;
    Pem& operator=(Pem&&) = delete;
};

inline std::string Pem::wrap(std::string_view label, const std::vector<std::byte>& der_bytes) {
    constexpr auto approximate_overhead = 40;
    constexpr auto base64_characters_per_line = 64;
    const auto base64_encoded = Base64::encode(der_bytes);
    std::string wrapped_pem;

    wrapped_pem.reserve((label.size() * 2) + base64_encoded.size() + approximate_overhead);
    wrapped_pem.append("-----BEGIN ");
    wrapped_pem.append(label);
    wrapped_pem.append("-----\n");

    std::size_t character_count = 0;

    for (const char character : base64_encoded) {
        wrapped_pem.push_back(character);
        character_count++;

        if (character_count == base64_characters_per_line) {
            wrapped_pem.push_back('\n');
            character_count = 0;
        }
    }

    if (character_count > 0) {
        wrapped_pem.push_back('\n');
    }

    wrapped_pem.append("-----END ");
    wrapped_pem.append(label);
    wrapped_pem.append("-----\n");

    return wrapped_pem;
}

inline std::string Pem::wrap(std::string_view label, std::string_view der_string) {
    std::vector<std::byte> bytes(der_string.size());

    std::transform(der_string.begin(), der_string.end(), bytes.begin(),
                   [](char character) { return static_cast<std::byte>(character); });
    return wrap(label, bytes);
}

inline Pem::Unwrapped Pem::unwrap(std::string_view pem_string) {
    constexpr std::string_view begin_prefix = "-----BEGIN ";
    constexpr std::string_view end_prefix = "-----END ";
    constexpr std::string_view suffix = "-----";
    const auto begin_position = pem_string.find(begin_prefix);

    if (begin_position == std::string_view::npos) {
        throw std::invalid_argument("PEM string is missing BEGIN header");
    }

    const auto label_start = begin_position + begin_prefix.size();
    const auto label_end = pem_string.find(suffix, label_start);

    if (label_end == std::string_view::npos) {
        throw std::invalid_argument("Malformed PEM BEGIN header");
    }

    std::string label(pem_string.substr(label_start, label_end - label_start));
    const auto expected_end = std::string(end_prefix) + label + std::string(suffix);
    const auto end_position = pem_string.find(expected_end, label_end);

    if (end_position == std::string_view::npos) {
        throw std::invalid_argument("PEM string missing matching END footer for label: "
                                    + label);
    }

    const auto content_start = label_end + suffix.size();
    const auto base64_content = pem_string.substr(content_start, end_position - content_start);

    std::string cleaned_base64;

    cleaned_base64.reserve(base64_content.size());

    std::copy_if(base64_content.begin(), base64_content.end(), std::back_inserter(cleaned_base64),
                 [](char character) {
        return character != ' ' && character != '\t' && character != '\r' && character != '\n';
    });

    return Unwrapped{std::move(label), Base64::decode(cleaned_base64)};
}


} // namespace crypto
