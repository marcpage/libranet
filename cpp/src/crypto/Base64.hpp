#pragma once

#include <cstddef>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace crypto {

class Base64 {
public:
    [[nodiscard]] static std::string encode(const std::vector<std::byte>& data);
    [[nodiscard]] static std::string encode(std::string_view data);
    [[nodiscard]] static std::vector<std::byte> decode(std::string_view encoded_string);

private:
    Base64() = delete;
    ~Base64() = delete;
    Base64(const Base64&) = delete;
    Base64& operator=(const Base64&) = delete;
    Base64(Base64&&) = delete;
    Base64& operator=(Base64&&) = delete;

    static constexpr std::string_view characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

    [[nodiscard]] static int get_character_value(char character);
};

inline std::string Base64::encode(const std::vector<std::byte>& data) {
    std::string encoded_result;
    // Only its low bits are read, so it may wrap around: unsigned, where
    // shifting a signed int past its top bit is undefined.
    unsigned int accumulator = 0;
    int bit_balance = -6;

    encoded_result.reserve(((data.size() + 2) / 3) * 4);

    for (auto byte_item : data) {
        accumulator = (accumulator << 8) + static_cast<unsigned char>(byte_item);
        bit_balance += 8;

        while (bit_balance >= 0) {
            encoded_result.push_back(characters[(accumulator >> bit_balance) & 0x3F]);
            bit_balance -= 6;
        }
    }

    if (bit_balance > -6) {
        encoded_result.push_back(characters[((accumulator << 8) >> (bit_balance + 8)) & 0x3F]);
    }

    while (encoded_result.size() % 4 != 0) {
        encoded_result.push_back('=');
    }

    return encoded_result;
}

inline std::string Base64::encode(std::string_view data) {
    std::vector<std::byte> bytes(data.size());

    for (std::size_t index = 0; index < data.size(); ++index) {
        bytes[index] = static_cast<std::byte>(data[index]);
    }

    return encode(bytes);
}

inline int Base64::get_character_value(char character) {
    if (character == '=') {
        return -1; // Represents padding
    }

    const auto found_position = characters.find(character);

    if (found_position == std::string_view::npos) {
        throw std::invalid_argument("Invalid Base64 character encountered.");
    }

    return static_cast<int>(found_position);
}

inline std::vector<std::byte> Base64::decode(std::string_view encoded_string) {
    if (encoded_string.size() % 4 != 0) {
        throw std::invalid_argument("Invalid Base64 string length.");
    }

    std::vector<std::byte> decoded_bytes;

    decoded_bytes.reserve((encoded_string.size() / 4) * 3);

    // Process input four characters at a time
    for (std::size_t index = 0; index < encoded_string.size(); index += 4) {
        int value_first = get_character_value(encoded_string[index]);
        int value_second = get_character_value(encoded_string[index + 1]);
        int value_third = get_character_value(encoded_string[index + 2]);
        int value_fourth = get_character_value(encoded_string[index + 3]);

        if (value_first == -1 || value_second == -1) {
            throw std::invalid_argument("Invalid Base64 padding position.");
        }

        // Combine the 6-bit values into a 24-bit integer block
        int triple = (value_first << 18) + (value_second << 12) + ((value_third != -1 ? value_third : 0) << 6) + (value_fourth != -1 ? value_fourth : 0);

        // Extract the decoded bytes based on padding presence
        decoded_bytes.push_back(static_cast<std::byte>((triple >> 16) & 0xFF));

        if (encoded_string[index + 2] != '=') {
            decoded_bytes.push_back(static_cast<std::byte>((triple >> 8) & 0xFF));
        }

        if (encoded_string[index + 3] != '=') {
            decoded_bytes.push_back(static_cast<std::byte>(triple & 0xFF));
        }
    }

    return decoded_bytes;
}

} // namespace crypto
