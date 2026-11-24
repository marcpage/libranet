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

// The searches of the peers for prefixes of hashes, which each search of this
// node starts, whoever asks it, so that what the peers answer is listed by a
// later one (HTTP API §6, High-Level Design §4.7). A search asks each peer it
// is given, those whose ids best match the prefix first, in
// peers.search_passes passes, network.retry_after_seconds apart, as each peer
// searches its own meanwhile, and posts what each answered as
// storage::SearchAnswered.
//
// Each peer is told in X-Search-Relays how many times more it may pass the
// search on: peers.search_relays for a search no peer asked, and for one a
// peer asked, one fewer than that peer said, or than peers.search_relays if
// it said more, so that a search ends unless a node still wants what it finds.
// A peer's search that may be passed on no more starts none. Nor does any
// search for a prefix while one for it is under way, or for
// peers.broadcast_hold_seconds after one ended, whoever asks, or peers still
// searching it would start it again, and it them, for ever. Safe to use from
// any thread.
class PeerSearch {
public:
    using Clock = std::chrono::steady_clock;
    using Milliseconds = std::chrono::milliseconds;
    using ClockFunction = std::function<Clock::time_point()>;
    using Sessions = std::vector<std::shared_ptr<PeerSession>>;

    // The peer, node, that asked this node a search, and how many times more
    // it said the search may be passed on, as its X-Search-Relays said.
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

        // peers.search_passes
        std::size_t passes = default_passes;
        // peers.search_relays
        std::size_t relays = default_relays;
        // network.retry_after_seconds: how long after one pass the next is
        // made.
        Milliseconds pass_interval = default_pass_interval;
        // peers.broadcast_hold_seconds: how long after a search ends another
        // for its prefix starts none.
        Milliseconds hold = default_hold;
    };

    // The protocol does not limit an answer once decompressed, so this does,
    // as the Python node's storage.max_decompressed_list_bytes does.
    static constexpr std::size_t max_decompressed_size = 4UL * types::Megabyte;

    // Throws std::invalid_argument, naming the setting, for fewer than two
    // passes, a pass interval less than zero, or a hold that is not more than
    // zero, as the Python node refuses them; or for clock with no function.
    explicit PeerSearch(const Settings& settings, ClockFunction clock = &Clock::now);
    ~PeerSearch() = default;
    // Its mutex is neither copied nor moved.
    PeerSearch(const PeerSearch&) = delete;
    PeerSearch& operator=(const PeerSearch&) = delete;
    PeerSearch(PeerSearch&&) = delete;
    PeerSearch& operator=(PeerSearch&&) = delete;

    // Starts a search of the peers for prefix, its first pass due at once:
    // one asker asked this node, or, with no asker, one this node or a client
    // of it asked. Whether it started: none does while one for prefix is
    // under way or held off, nor for an asker whose search may be passed on
    // no more.
    bool start(const storage::HashPrefix& prefix,
               const std::optional<Asker>& asker = std::nullopt);

    // Makes each pass due now, one after another, over sessions, the peers
    // connected now: asks each that is open, but the one that asked the
    // search, for its prefix, those whose ids best match it first, and posts
    // what each answered, unless it named nothing. A peer that cannot be
    // asked, or whose answer cannot be read, is passed over. After its last
    // pass, a search ends, and another for its prefix is held off. A pass one
    // thread is making is not made by another calling at once. Gives when the
    // next pass is due, or nothing when no search waits for one.
    std::optional<Clock::time_point> make_passes(const Sessions& sessions);

private:
    // A search under way, and its next pass, counted from one.
    struct Search {
        storage::HashPrefix prefix;
        std::optional<storage::Identifier> asker;
        std::size_t relays = 0; // As each peer is told
        std::size_t pass = 1;
        std::optional<Clock::time_point> due; // Nothing while a pass is made
    };

    // A pass taken to be made.
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
