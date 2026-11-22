#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <optional>

#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"

namespace eviction {

// How soon an object held goes (HighLevelDesign §4.5), weighed as the Python
// node weighs it: the higher its score, the sooner. Each of four factors is a
// fraction of one, measured against the most extreme object the node holds:
//
// - Unused: the time since it was last used, over the longest any object
//   held has gone unused.
// - Rarely asked for: one less how often it was asked for, over the most any
//   object held was.
// - Small: one less its size as stored, over the most an object may be
//   (1 MiB, HighLevelDesign §4.3), since a small object is quick to fetch
//   again and to move.
// - Far: one less how many leading bits of its hash match the node's id,
//   over the most any object held matches.
//
// A factor whose extreme is zero, as when nothing held was ever asked for, is
// one for every object. The four are multiplied, each first raised to at
// least a floor, so that no one factor keeps an object however the other
// three rank it.
class Score {
public:
    using Clock = std::chrono::system_clock;

    // The extremes of what the node holds, its own key aside, that each object
    // is measured against.
    struct Extremes {
        Clock::duration longest_unused{};   // Of the object held unused longest
        std::int64_t most_requests = 0;     // Of the object held asked for most
        std::size_t most_matching_bits = 0; // With the node's id
    };

    // The least any one factor counts for: an object stored at exactly 1 MiB
    // would otherwise score zero, whether it is asked for hourly or never.
    // Provisional, as the Python node's is.
    static constexpr double factor_floor = 0.01;
    // The least size counts for, so that being large keeps an object no more
    // than ten times as long as being small: most of what a node holds is
    // about 1 MiB, and were that worth as much as being in use, a small object
    // read every minute would go before large ones nothing has asked for in
    // hours, and be fetched again. Provisional, as the Python node's is.
    static constexpr double size_factor_floor = 0.1;

    // Scores the objects node_id holds, at now. Throws std::invalid_argument
    // for a node_id with no hash.
    Score(const storage::Identifier& node_id, Clock::time_point now, const Extremes& extremes);

    // The score of one object held, of size bytes as stored, asked for
    // requests times, and last used at last_used: when it was asked for or
    // stored, whichever was later. Unknown, it counts as unused the longest.
    // From the product of the floors to 1.
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
