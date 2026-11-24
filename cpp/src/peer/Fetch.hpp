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

// The searches of the connected peers for content the node lacks
// (High-Level Design §4.7). A search asks the peers this node has an
// outgoing connection to, those whose node ids share the most leading bits
// with the content's hash first, in passes: each in turn until one sends the
// content, then each again in each pass that follows, as it may have
// fetched the content meanwhile. A peer that connects during the search
// takes its place in the order. A peer that did not send the content is not
// asked again until the Retry-After it gave has passed, or this node's own
// if it gave none (HttpApi §5.2), and a pass waits for that no longer than
// this node's own. Once the last pass is over, a search that asked any peer
// starts none for the content for the failed search hold, since peers still
// searching would otherwise start it again, and it them, for ever.
//
// Content sent is checked against its identifier, as it came or once
// decompressed (High-Level Design §4.1.1), stored as it came, and posted as
// storage::DataStored, naming the peer it came from. Content the node comes
// to hold meanwhile, by any route, ends the search.
//
// Each search runs on the thread that asks for it, and any number may run
// at once, from any threads. It must end before this goes.
class Fetch {
public:
    using Clock = std::chrono::steady_clock;
    using Milliseconds = std::chrono::milliseconds;
    using ClockFunction = std::function<Clock::time_point()>;
    using Session = std::shared_ptr<PeerSession>;
    // The sessions with the peers this node has an outgoing connection to,
    // as they are now. Called from the threads searching, several at once.
    using GetPeers = std::function<std::vector<Session>()>;

    // How a search ended.
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

        // peers.search_passes
        std::size_t passes = default_passes;
        // peers.failed_search_hold_seconds
        Milliseconds failed_search_hold = default_failed_search_hold;
        // network.retry_after_seconds: how long a peer that gave no
        // Retry-After is left, and the longest a pass waits to begin.
        Milliseconds retry_after = default_retry_after;
        // How large content sent compressed may be once decompressed.
        std::size_t content_bytes = types::max_decompressed_content_size;
    };

    // Asks the peers get_peers gives for what is sought, and keeps what they
    // send in store. Throws std::invalid_argument, naming the setting, for
    // fewer than 2 passes, a retry after less than zero, or a failed search
    // hold not longer than a search may last: a retry after for each pass
    // after the first, as the peers' searches last on a network whose nodes
    // are set alike. Throws std::invalid_argument too for no store, or
    // get_peers or clock with no function.
    Fetch(storage::IDataSource::Ptr store, GetPeers get_peers, const Settings& settings,
          ClockFunction clock = &Clock::now);

    // Searches the peers for identifier's content, unless the node holds it,
    // a search for it is under way, or a new one is held off, and says how
    // it ended. Throws what the store throws, and std::bad_alloc if zlib
    // runs out of memory.
    Outcome fetch(const storage::Identifier& identifier);

    // Whether a new search for identifier would be held off now, as one that
    // found nothing ended lately.
    [[nodiscard]] bool is_held_off(const storage::Identifier& identifier) const;

    // Ends each search under way before it asks its next peer, or as it
    // waits for its next pass, and each asked for after.
    void stop();

    ~Fetch() = default;

    // Its searches hold this object, so it is neither copied nor moved.
    Fetch(const Fetch&) = delete;
    Fetch& operator=(const Fetch&) = delete;
    Fetch(Fetch&&) = delete;
    Fetch& operator=(Fetch&&) = delete;

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
