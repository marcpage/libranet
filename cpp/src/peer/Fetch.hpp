#pragma once

#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <vector>

#include "PeerSession.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"

namespace peer {

// The searches of the connected peers for content the node lacks (High-Level
// Design §4.7), in passes. Each runs on the thread that asks for it, and any
// number may run at once. Each must end before this goes.
class Fetch {
public:
    using Clock = std::chrono::steady_clock;
    using Milliseconds = std::chrono::milliseconds;
    using ClockFunction = std::function<Clock::time_point()>;
    using Session = std::shared_ptr<PeerSession>;
    // Called from the threads searching, several at once.
    using GetPeers = std::function<std::vector<Session>()>;

    enum class Outcome : std::uint8_t {
        Found,     // A peer sent the content, or the node came to hold it
        NotFound,  // The last pass ended without it
        Searching, // A search for it was under way already, and goes on
        HeldOff,   // A search for it found nothing lately
        Stopped,   // stop() ended it, or was called before it began
    };

    struct Settings {
        // As the Python node's are.
        static constexpr std::size_t default_passes = 3;
        static constexpr Milliseconds default_failed_search_hold = std::chrono::minutes(5);
        static constexpr Milliseconds default_retry_after = std::chrono::seconds(5);
        // As large as the Python node reads a bundle; only a safeguard
        // (HttpApi §21).
        static constexpr std::size_t default_content_bytes = 16 * types::Megabyte;

        std::size_t passes = default_passes;
        Milliseconds failed_search_hold = default_failed_search_hold;
        // How long a peer that gave no Retry-After is left, and the longest a
        // pass waits to begin.
        Milliseconds retry_after = default_retry_after;
        // Once decompressed.
        std::size_t content_bytes = default_content_bytes;
    };

    // Throws std::invalid_argument, naming the setting, for one it cannot use,
    // or for no store or a function missing.
    Fetch(storage::IDataSource::Ptr store, GetPeers get_peers, const Settings& settings,
          ClockFunction clock = &Clock::now);
    ~Fetch() = default;
    // Its searches hold this object.
    Fetch(const Fetch&) = delete;
    Fetch& operator=(const Fetch&) = delete;
    Fetch(Fetch&&) = delete;
    Fetch& operator=(Fetch&&) = delete;

    // Unless the node holds it, a search for it is under way, or a new one is
    // held off. Throws what the store throws, and std::bad_alloc if zlib runs
    // out of memory.
    Outcome fetch(const storage::Identifier& identifier);

    // As one that found nothing ended lately.
    [[nodiscard]] bool is_held_off(const storage::Identifier& identifier) const;

    // Ends each search under way, and each asked for after.
    void stop();

private:
    storage::IDataSource::Ptr m_store;
    GetPeers m_get_peers;
    Settings m_settings;
    ClockFunction m_clock;
    mutable std::mutex m_mutex;
    std::condition_variable m_stopping;              // stop() was called
    bool m_stopped{false};                           // Guarded by m_mutex
    std::set<std::string> m_under_way;               // Guarded by m_mutex
    std::map<std::string, Clock::time_point> m_held; // Guarded by m_mutex: until when

    [[nodiscard]] std::optional<Outcome> _start(const storage::Identifier& identifier);
    [[nodiscard]] Outcome _search(const storage::Identifier& identifier);
    [[nodiscard]] bool _is_stopped() const;
    void _wait_until(Clock::time_point until);
    void _hold_off(const storage::Identifier& identifier);
    void _end(const storage::Identifier& identifier);
};

} // namespace peer
