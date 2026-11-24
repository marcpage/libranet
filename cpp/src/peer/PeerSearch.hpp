#pragma once

#include <chrono>
#include <cstddef>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

#include "PeerSession.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"

namespace peer {

// The searches of the peers for prefixes of hashes, so that what they answer
// is listed by a later search of this node (HTTP API §6, High-Level Design
// §4.7). Safe to use from any thread.
class PeerSearch {
public:
    using Clock = std::chrono::steady_clock;
    using Milliseconds = std::chrono::milliseconds;
    using ClockFunction = std::function<Clock::time_point()>;
    using Sessions = std::vector<std::shared_ptr<PeerSession>>;

    // The peer that asked this node a search, and the X-Search-Relays it gave.
    struct Asker {
        storage::Identifier node;
        std::size_t relays = 0;
    };

    struct Settings {
        // As the Python node's are.
        static constexpr std::size_t default_passes = 3;
        static constexpr std::size_t default_relays = 2;
        static constexpr Milliseconds default_pass_interval = std::chrono::seconds(5);
        static constexpr Milliseconds default_hold = std::chrono::minutes(1);

        std::size_t passes = default_passes;
        std::size_t relays = default_relays;
        // How long after one pass the next is made.
        Milliseconds pass_interval = default_pass_interval;
        // How long after a search ends another for its prefix starts none.
        Milliseconds hold = default_hold;
        // The most an answer sent compressed may decompress to.
        std::size_t list_bytes = types::max_decompressed_list_size;
    };

    // Throws std::invalid_argument, naming the setting, for one the Python
    // node refuses, or for no clock.
    explicit PeerSearch(const Settings& settings, ClockFunction clock = &Clock::now);

    // Whether it started: none does while one for prefix is under way or held
    // off, nor for an asker whose search may be passed on no more.
    bool start(const storage::HashPrefix& prefix,
               const std::optional<Asker>& asker = std::nullopt);

    // Makes each pass due now over sessions, the peers connected now, and
    // gives when the next is due, or nothing when no search waits for one.
    std::optional<Clock::time_point> make_passes(const Sessions& sessions);

    ~PeerSearch() = default;

    PeerSearch(const PeerSearch&) = delete;
    PeerSearch& operator=(const PeerSearch&) = delete;
    PeerSearch(PeerSearch&&) = delete;
    PeerSearch& operator=(PeerSearch&&) = delete;

private:
    // Its next pass is counted from one.
    struct Search {
        storage::HashPrefix prefix;
        std::optional<storage::Identifier> asker;
        std::size_t relays = 0; // As each peer is told
        std::size_t pass = 1;
        std::optional<Clock::time_point> due; // Nothing while a pass is made
    };

    struct Pass {
        storage::HashPrefix prefix;
        std::optional<storage::Identifier> asker;
        std::size_t relays = 0;
    };

    Settings m_settings;
    ClockFunction m_clock;
    std::mutex m_mutex;
    std::map<std::string, Search> m_searches;        // By prefix; guarded by m_mutex
    std::map<std::string, Clock::time_point> m_held; // Until when, by prefix; guarded by m_mutex

    [[nodiscard]] std::optional<Pass> _take_due();
    void _made(const Pass& pass);
    [[nodiscard]] std::optional<Clock::time_point> _next_due();
};

} // namespace peer
