#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <optional>

#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"

namespace eviction {

// How soon an object held goes (HighLevelDesign §4.5), weighed as the Python
// node weighs it: the higher its score, the sooner.
class Scorer {
public:
    using Clock = std::chrono::system_clock;

    // Of what the node holds, its own key aside.
    struct Extremes {
        Clock::duration longest_unused{};   // Of the object held unused longest
        std::int64_t most_requests = 0;     // Of the object held asked for most
        std::size_t most_matching_bits = 0; // With the node's id
    };

    // Keeps a factor of zero, as for an object of exactly 1 MiB, from deciding
    // the score alone. Provisional, as the Python node's is.
    static constexpr double factor_floor = 0.01;
    // Keeps being large from counting for more than ten times being small.
    // Provisional, as the Python node's is.
    static constexpr double size_factor_floor = 0.1;

    // Throws std::invalid_argument for a node_id with no hash.
    Scorer(const storage::Identifier& node_id, Clock::time_point now, const Extremes& extremes);

    // From the product of the floors to 1. last_used is when it was last asked
    // for or stored; none counts as unused the longest.
    [[nodiscard]] double of(const storage::Identifier& identifier,
                            std::size_t size,
                            std::int64_t requests,
                            std::optional<Clock::time_point> last_used) const;

private:
    storage::HashPrefix m_node;
    Clock::time_point m_now;
    Extremes m_extremes;

    [[nodiscard]] double _unused(std::optional<Clock::time_point> last_used) const;
};

} // namespace eviction
