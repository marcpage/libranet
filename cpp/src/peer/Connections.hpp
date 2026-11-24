#pragma once

#include <chrono>
#include <functional>
#include <future>
#include <list>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <vector>

#include "Buckets.hpp"
#include "DialPlan.hpp"
#include "FirstContact.hpp"
#include "NodeListPublisher.hpp"
#include "PeerConnection.hpp"
#include "PeerSession.hpp"
#include "SeekFulfiller.hpp"
#include "identity/MessageVerifier.hpp"
#include "identity/NodeIdentity.hpp"
#include "msg/AsyncChannel.hpp"
#include "msg/IMessage.hpp"
#include "msg/Timer.hpp"
#include "protocol/NodeList.hpp"
#include "protocol/SeekList.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"

namespace peer {

// Owns the outgoing connections, and keeps up the peer mix (High-Level Design
// §4.6), dialing as the DialPlan says, each dial on a thread of its own. It
// dials nothing until the first candidates come.
class Connections {
public:
    using Candidate = DialPlan::Candidate;
    using Clock = DialPlan::Clock;
    using Milliseconds = DialPlan::Milliseconds;
    using ClockFunction = DialPlan::ClockFunction;

    // Events: plain structs, but for their conversion to text.
    // NOLINTBEGIN(*-non-private-member-variables-in-classes)

    struct PeersConnected {
        static constexpr auto name = "peers.connected";

        std::vector<storage::Identifier> node_ids;

        explicit operator std::string() const;
    };

    // NOLINTEND(*-non-private-member-variables-in-classes)

    // From --seed or peers.seed_file.
    struct Seed {
        std::string endpoint;
        std::optional<storage::Identifier> node_id = std::nullopt;
    };

    // How often, when nothing else happens, it looks for connections that have
    // closed and nodes whose rest is over.
    static constexpr Milliseconds default_check_interval{1'000};

    struct Settings {
        Buckets::ExpectedSizes sizes;
        DialPlan::Rests rests;
        PeerConnection::Limits limits;
        std::vector<Seed> seeds;
        Milliseconds check_interval = default_check_interval;
        SeekFulfiller::Settings fulfiller;
    };

    // identity must outlive it. A peer's key that is not held is put in store,
    // which must be the one verifier reads keys from. Throws
    // std::invalid_argument for a setting it cannot use.
    Connections(const identity::NodeIdentity& identity,
                const std::vector<std::string>& own_endpoints,
                const storage::IDataSource::Ptr& store, identity::MessageVerifier verifier,
                Settings settings, ClockFunction clock = &Clock::now);
    // Waits for each dial under way, which ends within the connect and request
    // timeouts, and closes every connection.
    ~Connections();

    // Those open now. Safe to call from any thread.
    [[nodiscard]] std::vector<std::shared_ptr<PeerSession>> open_sessions() const;

    // Safe to call from any thread.
    [[nodiscard]] std::vector<storage::Identifier> connected_ids() const;

    // Its threads hold this object, so it is neither copied nor moved.
    Connections(const Connections&) = delete;
    Connections& operator=(const Connections&) = delete;
    Connections(Connections&&) = delete;
    Connections& operator=(Connections&&) = delete;

private:
    static constexpr auto check_due = "peers.check_due";

    struct Peer {
        std::shared_ptr<PeerSession> session;
        std::string endpoint;
    };

    Milliseconds m_check_interval;
    storage::Identifier m_own_id;
    std::shared_ptr<SeekFulfiller> m_fulfiller; // Also first contact's
    FirstContact m_first_contact;
    NodeListPublisher m_publisher;
    PeerConnection::Limits m_limits;
    std::vector<Seed> m_seeds;
    Milliseconds m_seed_rest;
    ClockFunction m_clock;

    mutable std::mutex m_mutex;
    DialPlan m_plan;                                          // Guarded by m_mutex
    std::optional<std::vector<Candidate>> m_candidates;       // Guarded: none until the first
    protocol::NodeList m_own_list;                            // Guarded: posted at first contact
    protocol::SeekList m_own_seek;                            // Guarded: asked at first contact
    std::map<std::string, Peer> m_peers;                      // Guarded: by node id
    std::map<std::string, storage::Identifier> m_dialing;     // Guarded: by node id
    std::set<std::string> m_seeds_dialing;                    // Guarded: by endpoint
    std::map<std::string, Clock::time_point> m_seeds_resting; // Guarded: until, by endpoint
    bool m_refreshing = false;                                // Guarded: seek lists
    bool m_stopping = false;                                  // Guarded by m_mutex
    std::list<std::future<void>> m_jobs;                      // Guarded: dials, posts, reads

    // Made last, and so stopped first.
    msg::AsyncChannel m_channel;
    msg::Timer m_timer;

    void _handle(const msg::IMessage::Ptr& message);
    void _take_nodes(const protocol::NodeList& nodes, const protocol::NodeList& added);
    void _check_closed();
    void _refresh_seek_lists();
    void _maintain();
    void _dial_seeds();
    void _start(std::function<void()> job);
    void _dial(const Candidate& candidate);
    void _dial_seed(const Seed& seed);
    [[nodiscard]] bool _reach(const std::string& endpoint,
                              const std::optional<storage::Identifier>& expected);
    [[nodiscard]] bool _admit(const std::shared_ptr<PeerSession>& session,
                              const std::string& endpoint);
    void _post_connected() const;
    [[nodiscard]] std::vector<std::shared_ptr<PeerSession>> _open() const;
};

} // namespace peer
