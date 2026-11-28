#pragma once

#include <cstddef>
#include <set>
#include <string>
#include <string_view>
#include <vector>

#include "Body.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"

namespace protocol {

// A node list (HttpApi §10.6), best first. An endpoint named again keeps its
// first entry.
class NodeList {
public:
    struct Entry {
        std::string endpoint;
        storage::Identifier node_id;
    };

    using Entries = std::vector<Entry>;

    // As it is sent, compressed or not (HttpApi §10.6).
    static constexpr std::size_t max_size = 1 * types::Megabyte;

    // JSON, or zlib-compressed JSON of no more than decompressed_limit bytes.
    // An entry that is not valid is left out. Throws std::invalid_argument
    // for a body that is neither, or not an object holding a "nodes" object.
    [[nodiscard]] static NodeList from_body(
        std::string_view body, std::size_t decompressed_limit = types::max_decompressed_list_size);

    // Names own_endpoints first. Throws std::invalid_argument for an endpoint
    // that is not one.
    NodeList(const storage::Identifier& own_id, const std::vector<std::string>& own_endpoints);

    // Unless endpoint is already named. Throws std::invalid_argument for an
    // endpoint that is not one.
    void add(std::string endpoint, storage::Identifier node_id);

    [[nodiscard]] const Entries& entries() const noexcept;

    // As received from source_address, with each endpoint that names the
    // sender's machine resolved to it (HttpApi §10.2).
    [[nodiscard]] NodeList resolved(std::string_view source_address) const;

    // As JSON, with every entry.
    [[nodiscard]] std::string to_string() const;
    // Less than limit bytes: compressed only when too large, and leaving out
    // the entries at its end that do not fit even then. Throws
    // std::invalid_argument for a limit too small for an empty list.
    [[nodiscard]] Body body(std::size_t limit = max_size) const;

    NodeList() = default;

private:
    Entries m_entries;
    std::set<std::string> m_endpoints; // Those m_entries name
};

} // namespace protocol
