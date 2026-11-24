#pragma once

#include <cstddef>
#include <set>
#include <string>
#include <string_view>
#include <vector>

#include "storage/Identifier.hpp"
#include "types/Constants.hpp"

namespace protocol {

// A node list (HttpApi §10.6): the nodes a node knows, each by an endpoint it
// was reached at, as {"nodes": {"<endpoint>": "<node id>"}}. An endpoint is
// an http or https URL naming a host, and a port unless it is the scheme's
// own, and nothing more. Entries are kept in the order they were added, best
// first, and an endpoint named again keeps its first entry. A list a node
// sends names its own endpoints first (§10.1).
class NodeList {
public:
    using Data = std::vector<std::byte>;

    struct Entry {
        std::string endpoint;
        storage::Identifier node_id;
    };

    using Entries = std::vector<Entry>;

    // A list must be less than 1 MiB as it is sent, compressed or not
    // (HttpApi §10.6).
    static constexpr std::size_t max_size = 1 * types::Megabyte;
    // The protocol does not limit a list once decompressed, so this does, as
    // the Python node's max_decompressed_list_bytes does: a list is mostly
    // hex, so 1 MiB sent is about 2 MiB of list.
    static constexpr std::size_t max_decompressed_size = 4UL * types::Megabyte;

    // The list text names, as JSON. An entry whose endpoint is not one, or
    // whose node id is not a string naming an identifier, is left out,
    // rather than costing the sender the rest of its list; those under a
    // hash algorithm the node does not know are logged once for the list, as
    // they may mean the node needs updating. Throws std::invalid_argument
    // for text that is not JSON, or not an object holding a "nodes" object.
    [[nodiscard]] static NodeList parse(std::string_view text);
    // The list a body names, as it is sent: JSON, or a zlib stream of JSON
    // that holds no more than decompressed_limit bytes. Read as parse()
    // reads text, and throws std::invalid_argument as it does, or for a body
    // that is neither.
    [[nodiscard]] static NodeList from_body(const Data& body,
                                            std::size_t decompressed_limit = max_decompressed_size);

    // A list of this node's own, naming its endpoints first, in the order
    // given. Throws std::invalid_argument for an endpoint that is not one.
    NodeList(const storage::Identifier& own_id, const std::vector<std::string>& own_endpoints);

    // Names node_id at endpoint, after the entries already named, unless
    // endpoint is already named. Throws std::invalid_argument for an
    // endpoint that is not one.
    void add(std::string endpoint, storage::Identifier node_id);

    [[nodiscard]] const Entries& entries() const noexcept;

    // This list, as received over a connection from source_address, an IP
    // address, with the host of each endpoint that names the sender's
    // machine replaced by it (HttpApi §10.2): localhost, and a loopback
    // address unless source_address is one too, when the sender is on this
    // machine and the endpoint is kept as sent. An IPv4-mapped IPv6 address,
    // as a dual-stack listener reports an IPv4 client, is the IPv4 address
    // it holds. When source_address is no IP address, the entries that name
    // the sender's machine are left out.
    [[nodiscard]] NodeList resolved(std::string_view source_address) const;

    // The list as JSON, with every entry, in order.
    [[nodiscard]] std::string to_string() const;
    // The list as it is sent, less than limit bytes: its JSON, compressed
    // only when that is too large, and leaving out the entries at its end
    // that do not fit even then. Throws std::invalid_argument for a limit
    // too small to hold a list that names no node.
    [[nodiscard]] Data body(std::size_t limit = max_size) const;

    // A list naming no node, as a list received starts.
    NodeList() = default;

private:
    Entries m_entries;
    std::set<std::string> m_endpoints; // Those m_entries name
};

} // namespace protocol
