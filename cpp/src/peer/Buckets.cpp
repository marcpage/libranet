#include "Buckets.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "storage/Identifier.hpp"
#include "types/Converter.hpp"

using peer::Buckets;
using std::invalid_argument;
using std::min;
using std::nullopt;
using std::optional;
using std::set;
using std::size_t;
using std::string;
using std::string_view;
using std::to_string;
using std::uint64_t;
using std::vector;
using storage::Identifier;
using types::Converter;

namespace {

// A hex digit is four bits.
constexpr size_t bits_per_digit = 4;

// The most bits that tell one set's buckets apart, as the Python node
// allows: 65,536 buckets.
constexpr size_t max_prefix_bits = 16;

// How many buckets bits tell apart.
size_t bucket_count(size_t bits) {
    return size_t{1} << bits;
}

// Throws invalid_argument, naming setting, for bits that are not 1 to
// max_prefix_bits.
void check_prefix_bits(size_t bits, const string& setting) {
    if (bits < 1 || bits > max_prefix_bits) {
        throw invalid_argument(setting + " is not 1 to " + to_string(max_prefix_bits)
                               + ": " + to_string(bits));
    }
}

// sizes, once checked. Throws invalid_argument, naming the setting, for
// sizes a node cannot spread its server nodes by, as the Python node refuses them.
Buckets::ExpectedSizes checked(const Buckets::ExpectedSizes& sizes) {
    check_prefix_bits(sizes.primary_prefix_bits, "peers.bucket_prefix_bits");
    check_prefix_bits(sizes.secondary_prefix_bits, "peers.neighborhood_prefix_bits");

    const auto primary_buckets = bucket_count(sizes.primary_prefix_bits);

    if (sizes.min_primary_connections < 1 || sizes.min_primary_connections > primary_buckets) {
        throw invalid_argument("peers.min_outgoing_connections is not 1 to the buckets "
                               "peers.bucket_prefix_bits tells apart: "
                               + to_string(sizes.min_primary_connections));
    }

    if (sizes.min_secondary_connections > bucket_count(sizes.secondary_prefix_bits)) {
        throw invalid_argument("peers.min_neighborhood_connections is more than the buckets "
                               "peers.neighborhood_prefix_bits tells apart: "
                               + to_string(sizes.min_secondary_connections));
    }

    return sizes;
}

// The value of the first bits bits of node_id's hash. Throws
// invalid_argument for a hash shorter than that, as an identifier made with no hash has.
uint64_t leading_bits(const Identifier& node_id, size_t bits) {
    const auto& hash = node_id.hash();
    const auto digits = (bits + bits_per_digit - 1) / bits_per_digit;

    if (hash.size() < digits) {
        throw invalid_argument("A node id with no hash falls in no bucket");
    }

    const auto leading = string_view(hash).substr(0, digits);
    const auto value = Converter::parse_hex(leading);

    return value >> ((digits * bits_per_digit) - bits);
}

// The buckets, of the ones bits tells apart, that filled leaves empty,
// lowest first.
vector<size_t> unfilled(const set<size_t>& filled, size_t bits) {
    vector<size_t> empty;

    for (size_t bucket = 0; bucket < bucket_count(bits); ++bucket) {
        if (0 == filled.count(bucket)) {
            empty.push_back(bucket);
        }
    }

    return empty;
}

// How many more server nodes a set with filled buckets wants, to have as
// many as server_nodes.
size_t wanted(const set<size_t>& filled, size_t server_nodes) {
    return filled.size() < server_nodes ? server_nodes - filled.size() : 0;
}

} // namespace

namespace peer {

Buckets::Buckets(const Identifier& own_id, const ExpectedSizes& sizes)
    : m_sizes(checked(sizes))
    , m_own_bucket(primary_bucket(own_id)) {}

size_t Buckets::primary_bucket(const Identifier& node_id) const {
    return static_cast<size_t>(leading_bits(node_id, m_sizes.primary_prefix_bits));
}

optional<size_t> Buckets::secondary_bucket(const Identifier& node_id) const {
    const auto bits = m_sizes.secondary_prefix_bits;
    const auto mask = (uint64_t{1} << bits) - 1U;

    if (primary_bucket(node_id) != m_own_bucket) {
        return nullopt;
    }

    return static_cast<size_t>(leading_bits(node_id, m_sizes.primary_prefix_bits + bits) & mask);
}

Buckets::Gaps Buckets::gaps(const vector<Identifier>& server_ids) const {
    set<string> seen;
    set<size_t> primary;
    set<size_t> secondary;
    size_t in_secondary = 0;
    Gaps result;

    for (const auto& server_id : server_ids) {
        if (!seen.insert(server_id.to_string()).second) {
            continue;
        }

        if (const auto place = secondary_bucket(server_id)) {
            ++in_secondary;
            secondary.insert(*place);
        } else {
            primary.insert(primary_bucket(server_id));
        }
    }

    // A server node the secondary set has no room for, as a second in one of
    // its buckets, or one past the server nodes it is meant to have, fills
    // this node's own primary bucket.
    if (in_secondary > min(secondary.size(), m_sizes.min_secondary_connections)) {
        primary.insert(m_own_bucket);
    }

    result.primary.buckets = unfilled(primary, m_sizes.primary_prefix_bits);
    result.primary.wanted = wanted(primary, m_sizes.min_primary_connections);
    result.secondary.buckets = unfilled(secondary, m_sizes.secondary_prefix_bits);
    result.secondary.wanted = wanted(secondary, m_sizes.min_secondary_connections);
    return result;
}

} // namespace peer
