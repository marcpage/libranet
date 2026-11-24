#include "Connections.hpp"

#include <algorithm>
#include <exception>
#include <functional>
#include <future>
#include <list>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "DialPlan.hpp"
#include "FirstContact.hpp"
#include "NodeListPublisher.hpp"
#include "PeerConnection.hpp"
#include "PeerSession.hpp"
#include "SeekFulfiller.hpp"
#include "http/Url.hpp"
#include "identity/MessageVerifier.hpp"
#include "identity/NodeIdentity.hpp"
#include "logger/Logger.hpp"
#include "msg/Channel.hpp"
#include "msg/IMessage.hpp"
#include "msg/Message.hpp"
#include "msg/Timer.hpp"
#include "net/Address.hpp"
#include "protocol/NodeList.hpp"
#include "protocol/SeekList.hpp"
#include "stats/Recorder.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Converter.hpp"

using http::Url;
using identity::MessageVerifier;
using identity::NodeIdentity;
using msg::Channel;
using msg::IMessage;
using msg::Message;
using msg::Timer;
using msg::value_of;
using net::Address;
using peer::Connections;
using peer::PeerConnection;
using peer::PeerSession;
using peer::SeekFulfiller;
using protocol::NodeList;
using protocol::SeekList;
using stats::Recorder;
using std::any_of;
using std::exception;
using std::function;
using std::future;
using std::future_status;
using std::invalid_argument;
using std::make_shared;
using std::make_unique;
using std::nullopt;
using std::optional;
using std::scoped_lock;
using std::shared_ptr;
using std::string;
using std::string_view;
using std::to_string;
using std::vector;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;
using CandidatesUpdated = Recorder::CandidatesUpdated;
using ConnectionFailed = Recorder::ConnectionFailed;
using ConnectionOpened = Recorder::ConnectionOpened;
using NodesUpdated = Recorder::NodesUpdated;
using SeekUpdated = Recorder::SeekUpdated;


// The thread takes these events, which stats::Recorder posts from another
// file, so nothing here would make their messages' virtual to_string(),
// which value_of() casts to.
template class msg::Message<CandidatesUpdated>;
template class msg::Message<NodesUpdated>;
template class msg::Message<SeekUpdated>;

namespace {

using Milliseconds = Connections::Milliseconds;

// interval, once it is known to be more than zero. Throws invalid_argument
// when it is not.
Milliseconds checked_interval(Milliseconds interval) {
    if (interval <= Milliseconds::zero()) {
        throw invalid_argument("The interval connections are checked at is not more than zero: "
                               + to_string(interval.count()) + " ms");
    }

    return interval;
}

// The address endpoint names. Throws invalid_argument for one that is not
// an http URL naming a host, as the node dials only http (HttpApi §3.1).
Address address_of(const string& endpoint) {
    const auto url = Url::parse(endpoint);
    const auto port = url ? url->effective_port() : nullopt;

    if (!url || !port || "http" != Converter::lower_case(url->scheme()) || url->host().empty()) {
        throw invalid_argument(endpoint + " is not an http endpoint");
    }

    return {url->host(), *port};
}

// Reports what the thread could not handle.
void log_error(const string& text) {
    lErr << text;
}

} // namespace

namespace peer {

Connections::PeersConnected::operator string() const {
    return to_string(node_ids.size()) + " peers";
}

Connections::Connections(const NodeIdentity& identity, const vector<string>& own_endpoints,
                         const IDataSource::Ptr& store, MessageVerifier verifier, Settings settings,
                         Now now)
    : m_check_interval(checked_interval(settings.check_interval))
    , m_own_id(identity.id())
    , m_fulfiller(make_shared<SeekFulfiller>(store, settings.fulfiller))
    , m_first_contact(identity, store, std::move(verifier), m_fulfiller)
    , m_publisher(m_own_id, own_endpoints)
    , m_limits(settings.limits)
    , m_seeds(std::move(settings.seeds))
    , m_seed_rest(settings.rests.retry_delay)
    , m_now(now)
    , m_plan(m_own_id, settings.sizes, settings.rests, std::move(now))
    , m_own_list(m_own_id, own_endpoints)
    , m_channel({CandidatesUpdated::name, NodesUpdated::name, SeekUpdated::name, check_due},
                [this](const IMessage::Ptr& message) { _handle(message); }, log_error)
    , m_timer(check_due, m_check_interval) {
    const scoped_lock lock(m_mutex);

    // None yet: any named before a restart are gone.
    _post_connected();
}

Connections::~Connections() {
    {
        const scoped_lock lock(m_mutex);
        m_stopping = true;
    }

    m_timer.stop();
    m_channel.stop();

    // No job starts once it is stopping, so those taken are all there are.
    // Each connection is closed first, so that a job waiting on one ends.
    std::list<future<void>> jobs;

    {
        const scoped_lock lock(m_mutex);

        for (const auto& [node, peer] : m_peers) {
            peer.session->close();
        }

        jobs.swap(m_jobs);
    }

    for (const auto& job : jobs) {
        job.wait();
    }
}

void Connections::_handle(const IMessage::Ptr& message) {
    if (const auto* const candidates = value_of<CandidatesUpdated>(message)) {
        {
            const scoped_lock lock(m_mutex);
            m_candidates = candidates->candidates;
        }
        _maintain();
    } else if (const auto* const nodes = value_of<NodesUpdated>(message)) {
        _take_nodes(nodes->nodes, nodes->added);
    } else if (const auto* const seek = value_of<SeekUpdated>(message)) {
        const scoped_lock lock(m_mutex);
        m_own_seek = seek->seek;
    } else if (nullptr != value_of<Timer::Tick>(message)) {
        _check_closed();
        _maintain();
        _refresh_seek_lists();
    } else {
        lWarn << "Not an event connections takes:" << message->to_string();
    }
}

// Keeps nodes, to post at first contact, and posts added, the entries it
// newly names, to each node connected, on a thread of its own, so that a
// node slow to answer holds up nothing else.
void Connections::_take_nodes(const NodeList& nodes, const NodeList& added) {
    const scoped_lock lock(m_mutex);
    NodeListPublisher::Sessions sessions;

    m_own_list = nodes;

    for (const auto& [node, peer] : m_peers) {
        sessions.push_back(peer.session);
    }

    if (added.entries().empty() || sessions.empty()) {
        return;
    }

    // Captured as copies of their own, so that moving the job moves them.
    _start([this, to_post = added, sessions = std::move(sessions)] {
        (void)m_publisher.publish(to_post, sessions);
    });
}

// Lets go of each node whose connection has closed, resting it.
void Connections::_check_closed() {
    const scoped_lock lock(m_mutex);
    bool changed = false;

    for (auto peer = m_peers.begin(); peer != m_peers.end();) {
        const auto& session = *peer->second.session;

        if (session.is_open()) {
            ++peer;
            continue;
        }

        if (session.was_turned_away()) {
            lInfo << session.node_id().to_string() << "at" << peer->second.endpoint
                  << "turned this node away";
            m_plan.rest_turned_away(session.node_id());
        } else {
            lInfo << "The connection to" << session.node_id().to_string() << "at"
                  << peer->second.endpoint << "closed";
            m_plan.rest_failed(session.node_id());
        }

        peer = m_peers.erase(peer);
        changed = true;
    }

    if (changed) {
        _post_connected();
    }
}

// Reads again the seek lists of the nodes connected that are due, and
// fulfills them, on a thread of its own, unless that thread is still at it.
void Connections::_refresh_seek_lists() {
    const scoped_lock lock(m_mutex);

    if (m_stopping || m_refreshing || m_peers.empty()) {
        return;
    }

    m_refreshing = true;
    _start([this, open = _open()] {
        try {
            (void)m_fulfiller->refresh(open);
        } catch (const exception& error) {
            lErr << "Could not read the seek lists of the nodes connected:" << error.what();
        }

        const scoped_lock done(m_mutex);
        m_refreshing = false;
    });
}

// Dials what the plan says, once the candidates are known; or the seeds,
// while they name no node.
void Connections::_maintain() {
    const scoped_lock lock(m_mutex);

    if (m_stopping || !m_candidates) {
        return;
    }

    if (m_candidates->empty()) {
        _dial_seeds();
        return;
    }

    vector<Identifier> server_ids;

    server_ids.reserve(m_peers.size() + m_dialing.size());

    for (const auto& [node, peer] : m_peers) {
        server_ids.push_back(peer.session->node_id());
    }

    for (const auto& [node, node_id] : m_dialing) {
        server_ids.push_back(node_id);
    }

    for (const auto& candidate : m_plan.to_dial(*m_candidates, server_ids)) {
        m_dialing.emplace(candidate.node_id.to_string(), candidate.node_id);
        _start([this, to_dial = candidate] { _dial(to_dial); });
    }
}

// Dials each seed that is not being dialed, resting, or connected. Called
// holding m_mutex.
void Connections::_dial_seeds() {
    const auto now = m_now();

    for (const auto& seed : m_seeds) {
        const auto resting = m_seeds_resting.find(seed.endpoint);
        const auto resting_found = m_seeds_resting.end() != resting;
        const auto seed_dialing = 0 != m_seeds_dialing.count(seed.endpoint);
        const auto connected = any_of(m_peers.begin(), m_peers.end(), [&seed](const auto& entry) {
            return entry.second.endpoint == seed.endpoint;
        });

        if (connected || seed_dialing || (resting_found && now < resting->second)) {
            continue;
        }

        m_seeds_dialing.insert(seed.endpoint);
        _start([this, to_dial = seed] { _dial_seed(to_dial); });
    }
}

// Runs job on a thread of its own, unless it is stopping, and lets go of
// the jobs that have ended. Called holding m_mutex.
void Connections::_start(function<void()> job) {
    if (m_stopping) {
        return;
    }

    m_jobs.remove_if([](const future<void>& started) {
        return future_status::ready == started.wait_for(Milliseconds::zero());
    });
    m_jobs.push_back(std::async(std::launch::async, [job = std::move(job)] {
        try {
            job();
        } catch (const exception& error) {
            lErr << "Connections could not finish:" << error.what();
        }
    }));
}

// Dials candidate at each of its endpoints in turn, until one reaches it,
// and rests it when it is not connected then.
void Connections::_dial(const Candidate& candidate) {
    for (const auto& endpoint : candidate.endpoints) {
        if (_reach(endpoint, candidate.node_id)) {
            break;
        }

        const scoped_lock lock(m_mutex);

        if (m_stopping) {
            break;
        }
    }

    {
        const scoped_lock lock(m_mutex);
        const auto node = candidate.node_id.to_string();

        m_dialing.erase(node);

        if (0 == m_peers.count(node)) {
            m_plan.rest_failed(candidate.node_id);
        }
    }

    _maintain();
}

// Dials seed, and rests its endpoint, whatever came of it.
void Connections::_dial_seed(const Seed& seed) {
    (void)_reach(seed.endpoint, seed.node_id);

    {
        const scoped_lock lock(m_mutex);

        m_seeds_dialing.erase(seed.endpoint);
        m_seeds_resting[seed.endpoint] = m_now() + m_seed_rest;
    }

    _maintain();
}

// Dials endpoint, expecting the node expected there, or any node where it
// is none, and makes first contact. Posts what came of it, and takes the
// node reached in, if it is wanted. Says whether to dial the node's other
// endpoints no more: the node expected was reached, or another was taken
// in.
bool Connections::_reach(const string& endpoint, const optional<Identifier>& expected) {
    optional<FirstContact::Contact> contact;

    try {
        NodeList own_list;
        SeekList own_seek;

        {
            const scoped_lock lock(m_mutex);
            own_list = m_own_list;
            own_seek = m_own_seek;
        }

        contact.emplace(m_first_contact.make(
            make_unique<PeerConnection>(address_of(endpoint), m_limits), own_list, own_seek));
    } catch (const exception& error) {
        lInfo << "Could not reach" << endpoint + ":" << error.what();

        if (expected) {
            Channel::post(ConnectionFailed::name, ConnectionFailed{*expected, endpoint});
        }

        return false;
    }

    const auto node_id = contact->session.node_id();
    bool was_expected = true;

    if (expected && expected->to_string() != node_id.to_string()) {
        lInfo << endpoint << "answered as" << node_id.to_string() + ", not"
              << expected->to_string();
        Channel::post(ConnectionFailed::name, ConnectionFailed{*expected, endpoint});
        was_expected = false;
    }

    const auto admitted = _admit(make_shared<PeerSession>(std::move(contact->session)), endpoint);

    return admitted || was_expected;
}

// Takes session, reached at endpoint, in as a node connected, unless it is
// this node, is connected already, or this is stopping, when it is closed.
// A node other than this one proved its identity at endpoint all the same,
// which is posted. Says whether it was taken in.
bool Connections::_admit(const shared_ptr<PeerSession>& session, const string& endpoint) {
    const auto& node_id = session->node_id();
    const auto node = node_id.to_string();
    const auto is_this_node = node == m_own_id.to_string();
    string_view refusal;

    {
        const scoped_lock lock(m_mutex);

        if (m_stopping) {
            refusal = "connections are stopping";
        } else if (is_this_node) {
            refusal = "it is this node";
        } else if (0 != m_peers.count(node)) {
            refusal = "it is connected already";
        } else {
            m_peers.emplace(node, Peer{session, endpoint});
            _post_connected();
        }
    }

    if (!is_this_node) {
        Channel::post(ConnectionOpened::name, ConnectionOpened{node_id, endpoint});
    }

    if (!refusal.empty()) {
        lInfo << "Closing the connection to" << node << "at" << endpoint + ":" << refusal;
        session->close();
        return false;
    }

    lInfo << "Connected to" << node << "at" << endpoint;
    return true;
}

vector<shared_ptr<PeerSession>> Connections::open_sessions() const {
    const scoped_lock lock(m_mutex);

    return _open();
}

vector<Identifier> Connections::connected_ids() const {
    const scoped_lock lock(m_mutex);
    vector<Identifier> node_ids;

    node_ids.reserve(m_peers.size());

    for (const auto& [node, peer] : m_peers) {
        node_ids.push_back(peer.session->node_id());
    }

    return node_ids;
}

// The sessions of the nodes connected whose connections are open. Called
// holding m_mutex.
vector<shared_ptr<PeerSession>> Connections::_open() const {
    vector<shared_ptr<PeerSession>> open;

    open.reserve(m_peers.size());

    for (const auto& [node, peer] : m_peers) {
        if (peer.session->is_open()) {
            open.push_back(peer.session);
        }
    }

    return open;
}

// Posts the nodes connected now. Called holding m_mutex, so that each list
// goes out in the order the connections changed, and the last is current.
void Connections::_post_connected() const {
    PeersConnected connected;

    for (const auto& [node, peer] : m_peers) {
        connected.node_ids.push_back(peer.session->node_id());
    }

    Channel::post(PeersConnected::name, std::move(connected));
}

} // namespace peer
