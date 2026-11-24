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

// Seeks from the peers what the node lacks (High-Level Design §4.7), from a
// thread of its own. For each storage::DataNotFound, content a client asked
// for that the node does not hold, it fetches the content (Fetch), at most
// peers.search_workers at once, each on a thread of its own; those beyond
// wait their turn, in the order they came, each once. For each
// stats::Recorder::SearchRequested, it starts a search of the peers for the
// prefix (PeerSearch), and makes each pass of it as it comes due, on a
// thread of its own, so that a peer slow to answer a search holds up no
// fetch, nor a fetch a search.
class Seeker {
public:
    using Clock = PeerSearch::Clock;
    using Milliseconds = std::chrono::milliseconds;
    using Now = PeerSearch::Now;
    using Session = std::shared_ptr<PeerSession>;
    // The sessions with the peers this node has an outgoing connection to,
    // as they are now, to search. Called from the thread making the passes.
    using Peers = std::function<std::vector<Session>()>;

    struct Settings {
        // As the Python node's peers.search_workers is.
        static constexpr std::size_t default_workers = 8;
        // How often it looks for a pass that has come due.
        static constexpr Milliseconds default_check_interval{1'000};

        // peers.search_workers: the most fetches under way at once.
        std::size_t workers = default_workers;
        PeerSearch::Settings search;
        Milliseconds check_interval = default_check_interval;
    };

    // Fetches with fetch, which must outlive this, and searches the peers
    // peers gives. Throws std::invalid_argument, naming the setting, for no
    // workers, or what PeerSearch refuses; and for a check interval that is
    // not more than zero, or peers with no function.
    Seeker(Fetch& fetch, Peers peers, const Settings& settings, const Now& now = &Clock::now);
    // Stops its thread, and stops fetch, so that each fetch under way ends
    // before it asks its next peer; then waits for those fetches, and for the
    // passes under way, which end within the request timeout.
    ~Seeker();
    // Its threads hold this object, so it is neither copied nor moved.
    Seeker(const Seeker&) = delete;
    Seeker& operator=(const Seeker&) = delete;
    Seeker(Seeker&&) = delete;
    Seeker& operator=(Seeker&&) = delete;

private:
    // The name the timer posts under, for the thread to make the passes due.
    static constexpr auto pass_due = "peers.search_pass_due";

    // Mistaken: the constructor sets m_fetch and m_workers. cppcheck loses
    // the class's constructors in a file that names it with
    // `using peer::Seeker;`.
    // cppcheck-suppress uninitMemberVarPrivate
    Fetch* m_fetch;
    Peers m_peers;
    // cppcheck-suppress uninitMemberVarPrivate
    std::size_t m_workers;
    Now m_now;
    PeerSearch m_search;

    std::mutex m_mutex;
    std::deque<storage::Identifier> m_waiting; // Guarded by m_mutex: to fetch
    std::size_t m_fetching = 0;                // Guarded: fetches under way
    std::optional<Clock::time_point> m_due;    // Guarded: the next pass, if any
    bool m_passing = false;                    // Guarded: passes are being made
    bool m_pass_again = false;                 // Guarded: a search began meanwhile
    bool m_stopping = false;                   // Guarded by m_mutex
    std::list<std::future<void>> m_jobs;       // Guarded: fetches and passes

    // Made last, and so stopped first: the timer, then the thread, which
    // uses all of the above.
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
