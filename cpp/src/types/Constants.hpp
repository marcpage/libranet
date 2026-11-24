#pragma once

#include <string_view>

namespace types {

constexpr auto Kilobyte = 1024UL;
constexpr auto Megabyte = 1024UL * Kilobyte;

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
