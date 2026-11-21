#pragma once

#include <cstddef>
#include <optional>
#include <vector>

#include "storage/Identifier.hpp"

namespace peer {

// The buckets this node spreads its outgoing connections over
// (HighLevelDesign §4.6), in two sets. The first is told apart by the first
// bucket_prefix_bits bits of a node's id, so that the peers it is connected
// to cover the identifier space. The second, its neighborhood, is the nodes
// in this node's own bucket of the first set, told apart by the
// neighborhood_prefix_bits bits after those: they are responsible for much
// the same content as this node, so that content is one hop away.
//
// A connection fills one bucket at most. One to a neighbor fills its bucket
// of the neighborhood, if no other does, so this node's own bucket of the
// first set is filled only by a neighbor the neighborhood has no room for.
// cppcheck-suppress noConstructor
class Buckets {
public:
    // How many connections each set is meant to have, and how many bits
    // tell its buckets apart: the settings peers.<member>, with the Python
    // node's defaults.
    struct Sizes {
        // One connection for each bucket of each set.
        static constexpr std::size_t default_connections = 16;
        static constexpr std::size_t default_prefix_bits = 4;

        std::size_t min_outgoing_connections = default_connections;
        std::size_t bucket_prefix_bits = default_prefix_bits;
        std::size_t min_neighborhood_connections = default_connections;
        std::size_t neighborhood_prefix_bits = default_prefix_bits;
    };

    // The buckets of one set that no connection fills, and how many more
    // connections the set wants: as many as it has fewer buckets filled
    // than it is meant to have connections, so none once it has that many,
    // though some may still be empty.
    struct Empty {
        std::vector<std::size_t> buckets; // Lowest first
        std::size_t wanted = 0;
    };

    // What the connections leave empty, of each set.
    struct Gaps {
        Empty outgoing;
        Empty neighborhood;
    };

    // Throws std::invalid_argument, naming the setting, for prefix bits not
    // 1 to 16, for more connections in a set than it has buckets, or for no
    // outgoing connections; or for an own_id with no hash.
    Buckets(const storage::Identifier& own_id, const Sizes& sizes);

    // The bucket of the first set that node_id falls in: the value of its
    // first bucket_prefix_bits bits. Throws std::invalid_argument for a
    // node_id with no hash.
    [[nodiscard]] std::size_t bucket(const storage::Identifier& node_id) const;
    // The bucket of the neighborhood that node_id falls in: the value of the
    // neighborhood_prefix_bits bits after the first bucket_prefix_bits;
    // nothing when it is not in this node's own bucket of the first set.
    // Throws std::invalid_argument for a node_id with no hash.
    [[nodiscard]] std::optional<std::size_t>
    neighborhood_bucket(const storage::Identifier& node_id) const;
    // What the connections open now, to the nodes connected, leave empty.
    // A node named more than once fills one bucket. Throws
    // std::invalid_argument for a node id with no hash.
    [[nodiscard]] Gaps gaps(const std::vector<storage::Identifier>& connected) const;

private:
    Sizes m_sizes;
    // Mistaken: the constructor sets m_own_bucket. cppcheck loses the
    // class's constructors in a file that names it with
    // `using peer::Buckets;`.
    // cppcheck-suppress uninitMemberVarNoCtor
    std::size_t m_own_bucket;
};

} // namespace peer
