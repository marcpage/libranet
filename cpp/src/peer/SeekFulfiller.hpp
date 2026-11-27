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
#include "types/Constants.hpp"

namespace peer {

// Fulfills the seek lists of the nodes connected (High-Level Design §4.8,
// HttpApi §10.7.1). Any thread may use it.
//
// Mistaken: cppcheck loses the constructors in files `using` the class.
// cppcheck-suppress noConstructor
class SeekFulfiller {
public:
    using Clock = storage::SearchAnswers::Clock;
    using Milliseconds = std::chrono::milliseconds;
    using ClockFunction = std::function<Clock::time_point()>;
    using Sessions = std::vector<std::shared_ptr<PeerSession>>;

    struct Settings {
        // As the Python node's are.
        static constexpr Milliseconds default_refresh{30'000};
        static constexpr std::size_t default_pipeline_depth = 8;

        Milliseconds refresh = default_refresh;
        // The fewest leading bits what is named shares with a prefix that has
        // as many.
        std::size_t search_min_bits = storage::SearchAnswers::default_min_bits;
        // How many requests are sent at once, each holding its content until
        // it is answered.
        std::size_t pipeline_depth = default_pipeline_depth;
        // The most a list sent compressed may decompress to.
        std::size_t list_bytes = types::max_decompressed_list_size;
    };

    // Throws std::invalid_argument for a setting it cannot use, no store, or
    // no clock.
    SeekFulfiller(storage::IDataSource::Ptr store, const Settings& settings,
                  ClockFunction clock = &Clock::now);

    // Step 6 of first contact (Handshake Protocol §3). Throws what
    // PeerSession::exchange() throws.
    void fulfill(PeerSession& session, const protocol::SeekList& seek);

    // Reads again and fulfills each open session's list that is due. A
    // failure is logged. Gives how many lists were read.
    [[nodiscard]] std::size_t refresh(const Sessions& sessions);

    [[nodiscard]] std::size_t pipeline_depth() const noexcept;
    [[nodiscard]] std::size_t list_bytes() const noexcept;

    ~SeekFulfiller() = default;

    SeekFulfiller(const SeekFulfiller&) = delete;
    SeekFulfiller& operator=(const SeekFulfiller&) = delete;
    SeekFulfiller(SeekFulfiller&&) = delete;
    SeekFulfiller& operator=(SeekFulfiller&&) = delete;

private:
    // What has been pushed and named to one node, on the connection to it now.
    struct Served {
        std::set<std::string> pushed;                       // By identifier
        std::map<std::string, std::set<std::string>> named; // By prefix
        Clock::time_point read_at;
    };

    using Matches = std::map<std::string, std::vector<storage::Identifier>>; // By prefix
    using ByNode = std::map<std::string, std::shared_ptr<PeerSession>>;      // By node id

    storage::IDataSource::Ptr m_store;
    Settings m_settings;
    ClockFunction m_clock;
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
    [[nodiscard]] std::set<std::string> _pushed(const std::string& node_id);
    void _note_pushed(const std::string& node_id,
                      const std::vector<storage::Identifier>& identifiers);
    [[nodiscard]] Matches _unnamed(const std::string& node_id, const Matches& matches);
};

} // namespace peer
