#pragma once

#include <cstddef>
#include <string_view>

namespace types {

constexpr auto Kilobyte = 1024UL;
constexpr auto Megabyte = 1024UL * Kilobyte;
constexpr auto Gigabyte = 1024UL * Megabyte;

constexpr std::size_t bits_per_byte = 8;

// A node writes hex in lower case, and reads it in either.
constexpr std::string_view hex_digits = "0123456789abcdef";
constexpr std::string_view hex_digits_either_case = "0123456789abcdefABCDEF";
constexpr std::size_t bits_per_hex_digit = 4;
constexpr auto hex_digit_mask = 0x0FU;

// Safeguards, as the protocol sets no limit (HttpApi §21): a list as large as
// the Python node's storage.max_decompressed_list_bytes allows, and content as
// large as it reads a bundle.
constexpr std::size_t max_decompressed_list_size = 4 * Megabyte;
constexpr std::size_t max_decompressed_content_size = 16 * Megabyte;

// Read at a time where what is asked for may be more than the input holds.
constexpr std::size_t piece_size = 64 * Kilobyte;

// HttpApi §5, §7.
constexpr std::string_view json_content_type = "application/json";
constexpr std::string_view octet_stream_content_type = "application/octet-stream";

// HttpApi §5, §6, §10.5, §10.7.
constexpr std::string_view data_path_prefix = "/data/";
constexpr std::string_view search_path_prefix = "/data/search/";
constexpr std::string_view search_relays_header = "X-Search-Relays";
constexpr std::string_view nodes_path = "/data/nodes";
constexpr std::string_view seek_path = "/data/seek";

} // namespace types
