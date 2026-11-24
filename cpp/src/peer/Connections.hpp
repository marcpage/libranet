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

// Owns the outgoing connections, and keeps up the peer mix (High-Level
// Design §4.6). It dials as the DialPlan says whenever the candidates change
// (candidates.updated) or a connection opens, fails, or closes, and as each
// rest ends; and it dials the seeds only while the candidates name no node.
// It dials nothing until the first candidates come, so that it is made
// before stats::Recorder, which posts the first as it starts. Each node is
// dialed at its endpoints in turn, each on a thread of its own, until one
// reaches it, and first contact is made there (FirstContact), posting the
// node list last derived (nodes.updated), or this node's own endpoints
// alone before the first. The entries a node list newly names are posted to
// each node connected (NodeListPublisher).
//
// What came of each dial is posted for stats::Recorder: connection.opened
// for an endpoint that reached a node other than this one, which proved its
// identity there, and connection.failed for one that did not reach the node
// dialed, as when nothing answered or some other node did. A seed whose
// node id is not known is not failed, as there is no node to fail. The peers
// connected are posted as peers.connected as it starts, and whenever one
// joins or leaves. A node whose connection closed rests, as one that was
// reached at none of its endpoints does; a seed's endpoint rests after each
// dial.
class Connections {
public:
    using Candidate = DialPlan::Candidate;
    using Clock = DialPlan::Clock;
    using Milliseconds = DialPlan::Milliseconds;
    using ClockFunction = DialPlan::ClockFunction;

    // NOLINTBEGIN(*-non-private-member-variables-in-classes)

    // The nodes this node dialed and is connected to, which have changed.
    struct PeersConnected {
        static constexpr auto name = "peers.connected";

        std::vector<storage::Identifier> node_ids;

        explicit operator std::string() const;
    };

    // NOLINTEND(*-non-private-member-variables-in-classes)

    // A node to dial while no other is known, from --seed or
    // peers.seed_file: its endpoint, and its node id where that is known.
    struct Seed {
        std::string endpoint;
        std::optional<storage::Identifier> node_id = std::nullopt;
    };

    // How often, when nothing else happens, it looks for connections that
    // have closed and nodes whose rest is over.
    static constexpr Milliseconds default_check_interval{1'000};

    struct Settings {
        Buckets::ExpectedSizes sizes;
        DialPlan::Rests rests;
        PeerConnection::Limits limits;
        std::vector<Seed> seeds;
        Milliseconds check_interval = default_check_interval;
        SeekFulfiller::Settings fulfiller;
    };

    // Makes first contact as identity, which must outlive it, naming
    // own_endpoints as this node's, and checks answers with verifier. A
    // peer's key that is not held is put in store, which must be the store
    // verifier's keys are read from. Throws std::invalid_argument for what
    // DialPlan, FirstContact, or NodeListPublisher refuse, or a check
    // interval that is not more than zero.
    Connections(const identity::NodeIdentity& identity,
                const std::vector<std::string>& own_endpoints,
                const storage::IDataSource::Ptr& store, identity::MessageVerifier verifier,
                Settings settings, ClockFunction clock = &Clock::now);
    // Stops its threads, waits for each dial under way, which ends within
    // the connect and request timeouts, and closes every connection.
    ~Connections();
    // Its threads hold this object, so it is neither copied nor moved.
    Connections(const Connections&) = delete;
    Connections& operator=(const Connections&) = delete;
    Connections(Connections&&) = delete;
    Connections& operator=(Connections&&) = delete;

private:
    // The name the timer posts under, for the thread to check the
    // connections.
    static constexpr auto check_due = "peers.check_due";

    // A node connected, and the endpoint it was reached at.
    struct Peer {
        std::shared_ptr<PeerSession> session;
        std::string endpoint;
    };

    Milliseconds m_check_interval;
    storage::Identifier m_own_id;
    FirstContact m_first_contact;
    NodeListPublisher m_publisher;
    PeerConnection::Limits m_limits;
    std::vector<Seed> m_seeds;
    Milliseconds m_seed_rest;
    ClockFunction m_clock;

    std::mutex m_mutex;
    DialPlan m_plan;                                          // Guarded by m_mutex
    std::optional<std::vector<Candidate>> m_candidates;       // Guarded: none until the first
    protocol::NodeList m_own_list;                            // Guarded: posted at first contact
    protocol::SeekList m_own_seek;                            // Guarded: asked at first contact
    std::map<std::string, Peer> m_peers;                      // Guarded: by node id
    std::map<std::string, storage::Identifier> m_dialing;     // Guarded: by node id
    std::set<std::string> m_seeds_dialing;                    // Guarded: by endpoint
    std::map<std::string, Clock::time_point> m_seeds_resting; // Guarded: until, by endpoint
    bool m_stopping = false;                                  // Guarded by m_mutex
    std::list<std::future<void>> m_jobs;                      // Guarded: dials and posts

    // Made last, and so stopped first: the timer, then the thread, which
    // uses all of the above.
    msg::AsyncChannel m_channel;
    msg::Timer m_timer;

    void _handle(const msg::IMessage::Ptr& message);
    void _take_nodes(const protocol::NodeList& nodes, const protocol::NodeList& added);
    void _check_closed();
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
};

} // namespace peer
