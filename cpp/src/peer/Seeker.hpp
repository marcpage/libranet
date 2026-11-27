#pragma once

#include <chrono>
#include <cstddef>
#include <deque>
#include <functional>
#include <future>
#include <list>
#include <memory>
#include <mutex>
#include <optional>
#include <vector>

#include "Fetch.hpp"
#include "PeerSearch.hpp"
#include "PeerSession.hpp"
#include "msg/AsyncChannel.hpp"
#include "msg/IMessage.hpp"
#include "msg/Timer.hpp"
#include "stats/Recorder.hpp"
#include "storage/Identifier.hpp"

namespace peer {

// Seeks from the peers what the node lacks (High-Level Design §4.7).
class Seeker {
public:
    using Clock = PeerSearch::Clock;
    using Milliseconds = std::chrono::milliseconds;
    using ClockFunction = PeerSearch::ClockFunction;
    using Session = std::shared_ptr<PeerSession>;
    // Called from the thread making the passes.
    using GetPeers = std::function<std::vector<Session>()>;

    struct Settings {
        // As the Python node's peers.search_workers is.
        static constexpr std::size_t default_workers = 8;
        // How often it looks for a pass that has come due.
        static constexpr Milliseconds default_check_interval{1'000};

        // The most fetches under way at once.
        std::size_t workers = default_workers;
        PeerSearch::Settings search;
        Milliseconds check_interval = default_check_interval;
    };

    // fetch must outlive this. Throws std::invalid_argument for a setting it
    // cannot use, or a function missing.
    Seeker(Fetch& fetch, GetPeers get_peers, const Settings& settings,
           const ClockFunction& clock = &Clock::now);
    // Stops fetch, then waits for the fetches and passes under way, which end
    // within the request timeout.
    ~Seeker();

    // Its threads hold this object, so it is neither copied nor moved.
    Seeker(const Seeker&) = delete;
    Seeker& operator=(const Seeker&) = delete;
    Seeker(Seeker&&) = delete;
    Seeker& operator=(Seeker&&) = delete;

private:
    static constexpr auto pass_due = "peers.search_pass_due";

    // Mistaken: cppcheck loses the constructors in files `using` the class.
    // cppcheck-suppress uninitMemberVarPrivate
    Fetch* m_fetch;
    GetPeers m_get_peers;
    // cppcheck-suppress uninitMemberVarPrivate
    std::size_t m_workers;
    ClockFunction m_clock;
    PeerSearch m_search;

    std::mutex m_mutex;
    std::deque<storage::Identifier> m_waiting; // Guarded by m_mutex: to fetch
    std::size_t m_fetching = 0;                // Guarded: fetches under way
    std::optional<Clock::time_point> m_due;    // Guarded: the next pass, if any
    bool m_passing = false;                    // Guarded: passes are being made
    bool m_pass_again = false;                 // Guarded: a search began meanwhile
    bool m_stopping = false;                   // Guarded by m_mutex
    std::list<std::future<void>> m_jobs;       // Guarded: fetches and passes

    // Made last, and so stopped first.
    msg::AsyncChannel m_channel;
    msg::Timer m_timer;

    void _handle(const msg::IMessage::Ptr& message);
    void _lacked(const storage::Identifier& identifier);
    void _search(const stats::Recorder::SearchRequested& requested);
    void _pass_if_due();
    void _pass();
    void _fetch_from(storage::Identifier identifier);
    void _make_passes();
    void _start(std::function<void()> job);
};

} // namespace peer
