#pragma once

#include <algorithm>
#include <cstddef>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "types/Constants.hpp"

namespace crypto {

class Base64 {
public:
    [[nodiscard]] static std::string encode(const std::vector<std::byte>& data);
    [[nodiscard]] static std::string encode(std::string_view data);
    [[nodiscard]] static std::vector<std::byte> decode(std::string_view encoded_string);

    Base64() = delete;
    ~Base64() = delete;
    Base64(const Base64&) = delete;
    Base64& operator=(const Base64&) = delete;
    Base64(Base64&&) = delete;
    Base64& operator=(Base64&&) = delete;

private:
    static constexpr std::string_view characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                                                   "abcdefghijklmnopqrstuvwxyz0123456789+/";

    // Each group of 3 bytes is written as 4 characters of 6 bits each.
    static constexpr std::size_t bits_per_character = 6;
    static constexpr std::size_t bytes_per_group = 3;
    static constexpr std::size_t characters_per_group = 4;
    static constexpr unsigned int byte_mask = 0xFFU;
    static constexpr unsigned int character_mask = 0x3FU;
    static constexpr char padding = '=';
    // What _character_value() gives for padding.
    static constexpr int padding_value = -1;

    [[nodiscard]] static int _character_value(char character);
};

inline std::string Base64::encode(const std::vector<std::byte>& data) {
    std::string encoded_result;
    // Only its low bits are read, so it may wrap around: unsigned, where
    // shifting a signed int past its top bit is undefined.
    unsigned int accumulator = 0;
    // How many of the accumulator's low bits are yet to be written.
    std::size_t pending_bits = 0;

    encoded_result.reserve(((data.size() + bytes_per_group - 1) / bytes_per_group)
                           * characters_per_group);

    for (const auto byte_item : data) {
        accumulator = (accumulator << types::bits_per_byte)
                      | std::to_integer<unsigned int>(byte_item);
        pending_bits += types::bits_per_byte;

        while (pending_bits >= bits_per_character) {
            pending_bits -= bits_per_character;
            encoded_result.push_back(characters.at((accumulator >> pending_bits) & character_mask));
        }
    }

    if (pending_bits > 0) {
        encoded_result.push_back(
            characters.at((accumulator << (bits_per_character - pending_bits)) & character_mask));
    }

    while (encoded_result.size() % characters_per_group != 0) {
        encoded_result.push_back(padding);
    }

    return encoded_result;
}

inline std::string Base64::encode(std::string_view data) {
    std::vector<std::byte> bytes(data.size());

    std::transform(data.begin(), data.end(), bytes.begin(),
                   [](char character) { return static_cast<std::byte>(character); });
    return encode(bytes);
}

inline int Base64::_character_value(char character) {
    if (padding == character) {
        return padding_value;
    }

    const auto found_position = characters.find(character);

    if (std::string_view::npos == found_position) {
        throw std::invalid_argument("Invalid Base64 character encountered");
    }

    return static_cast<int>(found_position);
}

inline std::vector<std::byte> Base64::decode(std::string_view encoded_string) {
    if (encoded_string.size() % characters_per_group != 0) {
        throw std::invalid_argument("Invalid Base64 string length");
    }

    std::vector<std::byte> decoded_bytes;

    decoded_bytes.reserve((encoded_string.size() / characters_per_group) * bytes_per_group);

    // Process input four characters at a time
    for (std::size_t index = 0; index < encoded_string.size(); index += characters_per_group) {
        const auto group = encoded_string.substr(index, characters_per_group);
        // The group's 6-bit values, combined into a 24-bit block, with
        // padding as zeros.
        unsigned int triple = 0;

        for (std::size_t position = 0; position < characters_per_group; ++position) {
            const auto value = _character_value(group.at(position));

            // The first two characters of a group are never padding.
            if (padding_value == value && position < 2) {
                throw std::invalid_argument("Invalid Base64 padding position");
            }

            triple = (triple << bits_per_character)
                     | (padding_value == value ? 0U : static_cast<unsigned int>(value));
        }

        // Extract the decoded bytes based on padding presence
        decoded_bytes.push_back(
            static_cast<std::byte>((triple >> (2 * types::bits_per_byte)) & byte_mask));

        if (group.at(2) != padding) {
            decoded_bytes.push_back(
                static_cast<std::byte>((triple >> types::bits_per_byte) & byte_mask));
        }

        if (group.at(3) != padding) {
            decoded_bytes.push_back(static_cast<std::byte>(triple & byte_mask));
        }
    }

    return decoded_bytes;
}

} // namespace crypto
