#pragma once

#include <cstddef>
#include <optional>
#include <vector>

#include "storage/Identifier.hpp"

namespace peer {

// Given a list of server ids we are connected to, tells you which ids we still need.
// Given a server id, tells you which bucket it fills.
// cppcheck-suppress noConstructor
class Buckets {
public:
    struct ExpectedSizes {
        // One server node for each bucket of each set.
        static constexpr std::size_t default_connections = 16;
        static constexpr std::size_t default_prefix_bits = 4;

        // peers.min_outgoing_connections
        std::size_t min_primary_connections = default_connections;
        // peers.bucket_prefix_bits
        std::size_t primary_prefix_bits = default_prefix_bits;
        // peers.min_neighborhood_connections
        std::size_t min_secondary_connections = default_connections;
        // peers.neighborhood_prefix_bits
        std::size_t secondary_prefix_bits = default_prefix_bits;
    };

    struct Missing {
        std::vector<std::size_t> buckets; // Lowest first
        std::size_t wanted = 0;
    };

    struct Gaps {
        Missing primary;
        Missing secondary;
    };

    // Throws std::invalid_argument, naming the setting, for prefix bits not
    // 1 to 16, for more server nodes in a set than it has buckets, or for
    // none in the primary set; or for an own_id with no hash.
    Buckets(const storage::Identifier& own_id, const ExpectedSizes& sizes);

    // Throws std::invalid_argument for a node_id with no hash.
    [[nodiscard]] std::size_t primary_bucket(const storage::Identifier& node_id) const;

    // Throws std::invalid_argument for a node_id with no hash.
    [[nodiscard]] std::optional<std::size_t>
    secondary_bucket(const storage::Identifier& node_id) const;

    // Throws std::invalid_argument for a node id with no hash.
    [[nodiscard]] Gaps gaps(const std::vector<storage::Identifier>& server_ids) const;

private:
    ExpectedSizes m_sizes;
    std::size_t m_own_bucket{}; // the bucket secondary slots into primary
};

} // namespace peer
