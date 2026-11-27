#pragma once

#include <cstddef>
#include <optional>
#include <vector>

#include "storage/Identifier.hpp"

namespace peer {

// Which bucket a node fills, and which the server nodes connected leave empty.
class Buckets {
public:
    struct ExpectedSizes {
        // One server node for each bucket of each set.
        static constexpr std::size_t default_connections = 16;
        static constexpr std::size_t default_prefix_bits = 4;

        std::size_t min_primary_connections = default_connections;
        std::size_t primary_prefix_bits = default_prefix_bits;
        std::size_t min_secondary_connections = default_connections;
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

    // Throws std::invalid_argument for sizes it cannot use, or an own_id with
    // no hash.
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
