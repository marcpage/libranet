#pragma once

#include <chrono>
#include <cstddef>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <vector>

#include "PeerSession.hpp"
#include "protocol/SeekList.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "storage/SearchAnswers.hpp"

namespace peer {

// Fulfills what the nodes this node is connected to seek (High-Level Design
// §4.8). It reads each node's seek list (HttpApi §10.7.1) at first contact,
// and again every peers.seek_refresh_seconds while the connection lasts
// (Handshake Protocol §3.3), and puts what it holds of the data entries
// (§3, step 6). For each search entry, it posts the identifiers of what it
// holds that a search of the prefix finds (HttpApi §6), best match first,
// for the node to list by identifier what it lacks and wants, which a later
// read fulfills (HttpApi §10.7.2). It names each once while the entry stays
// listed, and none it has pushed on that connection, and pushes each once on
// it. What a search finds is kept for peers.seek_refresh_seconds, for every
// connection to share, since each node's list is read that often, and nodes
// seek much the same. Any thread may use it.
//
// Mistaken: it declares a constructor. cppcheck loses the class's
// constructors in a file that names it with `using peer::SeekFulfiller;`.
// cppcheck-suppress noConstructor
class SeekFulfiller {
public:
    using Clock = storage::SearchAnswers::Clock;
    using Milliseconds = std::chrono::milliseconds;
    using Now = std::function<Clock::time_point()>;
    using Sessions = std::vector<std::shared_ptr<PeerSession>>;

    struct Settings {
        // As the Python node's are.
        static constexpr Milliseconds default_refresh{30'000};
        static constexpr std::size_t default_search_min_bits = 24;
        static constexpr std::size_t default_pipeline_depth = 8;

        // peers.seek_refresh_seconds: how often a node's seek list is read.
        Milliseconds refresh = default_refresh;
        // storage.search_min_bits: the fewest leading bits what is named
        // shares with a prefix that has as many.
        std::size_t search_min_bits = default_search_min_bits;
        // peers.pipeline_depth: how many requests are sent at once, each put
        // holding its content in memory until it is answered.
        std::size_t pipeline_depth = default_pipeline_depth;
    };

    // Puts and names what store holds. Throws std::invalid_argument, naming
    // the setting, for a refresh that is not more than zero or a pipeline
    // depth of none; or for no store, or now with no function.
    SeekFulfiller(storage::IDataSource::Ptr store, const Settings& settings,
                  Now now = &Clock::now);
    ~SeekFulfiller() = default;
    // Its mutex is neither copied nor moved.
    SeekFulfiller(const SeekFulfiller&) = delete;
    SeekFulfiller& operator=(const SeekFulfiller&) = delete;
    SeekFulfiller(SeekFulfiller&&) = delete;
    SeekFulfiller& operator=(SeekFulfiller&&) = delete;

    // Step 6 of first contact with the node of session, on a new connection:
    // fulfills seek, the list it has just sent. What was pushed and named to
    // the node on a connection before is let go. Its list is due to be read
    // again once peers.seek_refresh_seconds have passed. Throws what
    // PeerSession::exchange() throws.
    void fulfill(PeerSession& session, const protocol::SeekList& seek);

    // Reads again the seek list of each of sessions that is open, whose list
    // was read peers.seek_refresh_seconds ago or more, or not at all, and
    // fulfills it, each on a thread of its own, so that a node slow to answer
    // holds up none of the others, and waits for each. What was pushed and
    // named to a node none of sessions is open to is let go once its list is
    // due, as its connection has ended. A list that cannot be read, or a
    // request that fails, is logged. Gives how many lists were read.
    [[nodiscard]] std::size_t refresh(const Sessions& sessions);

    // peers.pipeline_depth.
    [[nodiscard]] std::size_t pipeline_depth() const noexcept;

private:
    // What has been pushed and named to one node, on the connection to it
    // now, and when its list was last read.
    struct Served {
        std::set<std::string> pushed;                       // By identifier
        std::map<std::string, std::set<std::string>> named; // By prefix
        Clock::time_point read_at;
    };

    using Matches = std::map<std::string, std::vector<storage::Identifier>>; // By prefix
    using ByNode = std::map<std::string, std::shared_ptr<PeerSession>>;      // By node id

    storage::IDataSource::Ptr m_store;
    Settings m_settings;
    Now m_now;
    storage::SearchAnswers m_found;
    std::mutex m_mutex;
    std::map<std::string, Served> m_served; // By node id; guarded by m_mutex

    [[nodiscard]] Sessions _due(const Sessions& sessions);
    void _let_go(const ByNode& open, Clock::time_point now);
    [[nodiscard]] bool _refresh(PeerSession& session);
    void _fulfill(PeerSession& session, const protocol::SeekList& seek);
    void _push(PeerSession& session, const std::vector<storage::Identifier>& sought);
    void _push_run(PeerSession& session, const std::vector<storage::Identifier>& run);
    void _name(PeerSession& session, const std::vector<storage::HashPrefix>& searches);
    [[nodiscard]] Matches _matches(const std::vector<storage::HashPrefix>& searches,
                                   const std::set<std::string>& pushed);
    [[nodiscard]] std::set<std::string> _pushed(const std::string& node_id);
    void _note_pushed(const std::string& node_id,
                      const std::vector<storage::Identifier>& identifiers);
    [[nodiscard]] Matches _unnamed(const std::string& node_id, const Matches& matches);
};

} // namespace peer
