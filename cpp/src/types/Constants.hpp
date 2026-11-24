#pragma once

#include <cstddef>
#include <string_view>

namespace types {

constexpr auto Kilobyte = 1024UL;
constexpr auto Megabyte = 1024UL * Kilobyte;
constexpr auto Gigabyte = 1024UL * Megabyte;

constexpr std::size_t bits_per_byte = 8;

// The digits of hex, in the lower case a node writes them in, and in either
// case, as a node reads them. Each is four bits, which the mask keeps.
constexpr std::string_view hex_digits = "0123456789abcdef";
constexpr std::string_view hex_digits_either_case = "0123456789abcdefABCDEF";
constexpr std::size_t bits_per_hex_digit = 4;
constexpr auto hex_digit_mask = 0x0FU;

// How large a list and content sent compressed may be once decompressed,
// unless the node is told otherwise. The protocol sets no limit; these are
// only safeguards (HttpApi §21). A list, which is mostly hex, so that 1 MiB
// sent is about 2 MiB of list, may be as large as the Python node's
// storage.max_decompressed_list_bytes allows, and content as large as the
// Python node reads a bundle.
constexpr std::size_t max_decompressed_list_size = 4 * Megabyte;
constexpr std::size_t max_decompressed_content_size = 16 * Megabyte;

// How much is read at a time, where what is asked for may be more than the
// input holds, so that it takes no more memory than the input has.
constexpr std::size_t piece_size = 64 * Kilobyte;

// The media types of what nodes send one another: JSON, and content, which
// is put and served as it is stored (HttpApi §5, §7).
constexpr std::string_view json_content_type = "application/json";
constexpr std::string_view octet_stream_content_type = "application/octet-stream";

// Where content is, followed by its identifier (HttpApi §5); where a search
// is asked for, followed by its prefix, and the header that says how many
// times more it may be passed on (HttpApi §6); and where a node serves its
// node list and its seek list, and a peer posts its own (HttpApi §10.5,
// §10.7).
constexpr std::string_view data_path_prefix = "/data/";
constexpr std::string_view search_path_prefix = "/data/search/";
constexpr std::string_view search_relays_header = "X-Search-Relays";
constexpr std::string_view nodes_path = "/data/nodes";
constexpr std::string_view seek_path = "/data/seek";

} // namespace types
