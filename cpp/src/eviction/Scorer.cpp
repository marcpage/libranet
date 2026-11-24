#include "Scorer.hpp"

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <optional>

#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"

using eviction::Scorer;
using std::clamp;
using std::int64_t;
using std::optional;
using std::size_t;
using std::chrono::duration;
using storage::HashPrefix;
using storage::Identifier;
using types::Megabyte;

namespace {

using Clock = Scorer::Clock;

// part as a fraction of whole, kept within 0 and 1; 0 of a whole of 0.
double share(double part, double whole) {
    if (whole <= 0.0) {
        return 0.0;
    }

    return clamp(part / whole, 0.0, 1.0);
}

// fraction, of one, raised to at least least, with one left at one.
double raised(double fraction, double least) {
    return least + ((1.0 - least) * fraction);
}

// The time from last_used to now, over longest_unused, the longest any object
// held has gone unused; one when either is unknown or none has gone unused at
// all.
double unused(Clock::time_point now, Clock::duration longest_unused,
              optional<Clock::time_point> last_used) {
    if (!last_used.has_value() || longest_unused <= Clock::duration::zero()) {
        return 1.0;
    }

    return share(duration<double>(now - *last_used).count(),
                 duration<double>(longest_unused).count());
}

} // namespace

Scorer::Scorer(const Identifier& node_id, Clock::time_point now, const Extremes& extremes)
    : m_node(HashPrefix::parse(node_id.hash()))
    , m_now(now)
    , m_extremes(extremes) {}

double Scorer::of(const Identifier& identifier,
                  size_t size,
                  int64_t requests,
                  optional<Clock::time_point> last_used) const {
    const auto rarely_asked_for = 1.0
                                  - share(static_cast<double>(requests),
                                          static_cast<double>(m_extremes.most_requests));
    const auto small = 1.0
                       - share(static_cast<double>(size), static_cast<double>(Megabyte));
    const auto far = 1.0
                     - share(static_cast<double>(m_node.matching_bits(identifier)),
                             static_cast<double>(m_extremes.most_matching_bits));
    const auto idle = unused(m_now, m_extremes.longest_unused, last_used);

    // Multiplied in the order the Python node multiplies them, so that the
    // two score alike to the last bit.
    return raised(small, size_factor_floor)
           * (raised(idle, factor_floor) * raised(rarely_asked_for, factor_floor)
              * raised(far, factor_floor));
}
