#pragma once

#include <cstddef>
#include <optional>
#include <vector>

#include "storage/Identifier.hpp"

namespace peer {

// The buckets that this node's server nodes, the nodes it connects to, are
// spread over (HighLevelDesign §4.6), in two sets. The primary set is told
// apart by the first primary_prefix_bits bits of a node's id, so that its
// server nodes cover the identifier space. The secondary set is the nodes in
// this node's own primary bucket, told apart by the secondary_prefix_bits
// bits after those: they are responsible for much the same content as this
// node, so that content is one hop away.
//
// A server node fills one bucket at most. One in this node's own primary
// bucket fills its secondary bucket, if no other does, so this node's own
// primary bucket is filled only by a server node the secondary set has no
// room for.
// cppcheck-suppress noConstructor
class Buckets {
public:
    // How many server nodes each set is meant to have, and how many bits
    // tell its buckets apart, with the Python node's defaults. Each is the
    // setting named above it, as the Python node names it.
    struct Sizes {
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

    // The buckets of one set that no server node fills, and how many more
    // server nodes the set wants: as many as it has fewer buckets filled
    // than it is meant to have server nodes, so none once it has that many,
    // though some may still be empty.
    struct Empty {
        std::vector<std::size_t> buckets; // Lowest first
        std::size_t wanted = 0;
    };

    // What the server nodes leave empty, of each set.
    struct Gaps {
        Empty primary;
        Empty secondary;
    };

    // Throws std::invalid_argument, naming the setting, for prefix bits not
    // 1 to 16, for more server nodes in a set than it has buckets, or for
    // none in the primary set; or for an own_id with no hash.
    Buckets(const storage::Identifier& own_id, const Sizes& sizes);

    // The primary bucket that node_id falls in: the value of its first
    // primary_prefix_bits bits. Throws std::invalid_argument for a node_id
    // with no hash.
    [[nodiscard]] std::size_t primary_bucket(const storage::Identifier& node_id) const;
    // The secondary bucket that node_id falls in: the value of the
    // secondary_prefix_bits bits after the first primary_prefix_bits;
    // nothing when it is not in this node's own primary bucket. Throws
    // std::invalid_argument for a node_id with no hash.
    [[nodiscard]] std::optional<std::size_t>
    secondary_bucket(const storage::Identifier& node_id) const;
    // What the server nodes this node is connected to now, named by
    // server_ids, leave empty. One named more than once fills one bucket.
    // Throws std::invalid_argument for a node id with no hash.
    [[nodiscard]] Gaps gaps(const std::vector<storage::Identifier>& server_ids) const;

private:
    Sizes m_sizes;
    // Mistaken: the constructor sets m_own_bucket. cppcheck loses the
    // class's constructors in a file that names it with
    // `using peer::Buckets;`.
    // cppcheck-suppress uninitMemberVarNoCtor
    std::size_t m_own_bucket;
};

} // namespace peer
