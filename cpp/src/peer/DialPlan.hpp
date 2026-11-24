#pragma once

#include <chrono>
#include <functional>
#include <map>
#include <string>
#include <vector>

#include "Buckets.hpp"
#include "stats/NodeAddresses.hpp"
#include "storage/Identifier.hpp"

namespace peer {

// Which candidates to dial now, to fill the buckets this node's server nodes
// leave empty (High-Level Design §4.6), resting each that was not reached or
// turned this node away (Handshake Protocol §5.1).
class DialPlan {
public:
    using Candidate = stats::NodeAddresses::Candidate;
    using Clock = std::chrono::steady_clock;
    using Milliseconds = std::chrono::milliseconds;
    using ClockFunction = std::function<Clock::time_point()>;

    struct Rests {
        // As the Python node's are.
        static constexpr Milliseconds default_retry_delay = std::chrono::minutes(1);
        static constexpr Milliseconds default_turned_away_rest = std::chrono::hours(1);

        Milliseconds retry_delay = default_retry_delay;
        Milliseconds turned_away_rest = default_turned_away_rest;
    };

    // Throws std::invalid_argument, naming the setting, for one it cannot use,
    // or for an own_id with no hash, or no clock.
    DialPlan(const storage::Identifier& own_id, const Buckets::ExpectedSizes& sizes,
             const Rests& rests, ClockFunction clock = &Clock::now);

    // server_ids are the nodes connected or being dialed; candidates are best
    // first, as NodeAddresses::candidates() gives them. Throws
    // std::invalid_argument for a node id with no hash.
    [[nodiscard]] std::vector<Candidate>
    to_dial(const std::vector<Candidate>& candidates,
            const std::vector<storage::Identifier>& server_ids) const;

    // It was reached at none of its endpoints, or its connection closed.
    void rest_failed(const storage::Identifier& node_id);

    // It closed the connection once it had answered (Handshake Protocol §5.2).
    void rest_turned_away(const storage::Identifier& node_id);

private:
    Buckets m_buckets;
    std::string m_own_id;
    Rests m_rests;
    ClockFunction m_clock;
    std::map<std::string, Clock::time_point> m_resting; // Until when, by node id
};

} // namespace peer
