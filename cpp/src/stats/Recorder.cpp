#include "Recorder.hpp"

#include <algorithm>
#include <filesystem>
#include <iterator>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "ContentUses.hpp"
#include "NodeAddresses.hpp"
#include "Sought.hpp"
#include "db/Database.hpp"
#include "logger/Logger.hpp"
#include "msg/Channel.hpp"
#include "msg/IMessage.hpp"
#include "msg/Message.hpp"
#include "msg/Timer.hpp"
#include "protocol/NodeList.hpp"
#include "protocol/SeekList.hpp"
#include "storage/DataNotFound.hpp"
#include "storage/DataRequested.hpp"
#include "storage/DataStored.hpp"
#include "storage/Identifier.hpp"

using db::Database;
using msg::Channel;
using msg::IMessage;
using msg::Message;
using msg::Timer;
using protocol::NodeList;
using protocol::SeekList;
using stats::ContentUses;
using stats::NodeAddresses;
using stats::Recorder;
using stats::Sought;
using std::back_inserter;
using std::equal;
using std::inserter;
using std::invalid_argument;
using std::map;
using std::pair;
using std::set;
using std::string;
using std::to_string;
using std::transform;
using std::vector;
using std::filesystem::path;
using storage::DataNotFound;
using storage::DataRequested;
using storage::DataStored;
using storage::Identifier;

// The thread takes these events, which others post from other files, so
// nothing here would make their messages' virtual to_string(), which
// value_of() casts to.
template class msg::Message<DataNotFound>;
template class msg::Message<DataRequested>;
template class msg::Message<DataStored>;
template class msg::Message<Recorder::ConnectionFailed>;
template class msg::Message<Recorder::ConnectionOpened>;
template class msg::Message<Recorder::NodesReceived>;
template class msg::Message<Recorder::SearchRequested>;

namespace {

using Candidate = NodeAddresses::Candidate;
using Milliseconds = Recorder::Milliseconds;
using Source = NodeAddresses::Source;

// The value message holds, if it is a Type.
template<typename Type>
const Type* value_of(const IMessage::Ptr& message) {
    const auto* const typed = dynamic_cast<const Message<Type>*>(message.get());

    return nullptr == typed ? nullptr : &typed->value();
}

// Runs record in a transaction of its own on database, so that what it
// records is kept whole or not at all.
template<typename Function>
void in_transaction(Database& database, const Function& record) {
    Database::Transaction transaction(database);

    record();
    transaction.commit();
}

// The entries received names, in a list for each way they were learned.
map<Source, NodeList> lists_by_source(const Recorder::NodesReceived& received) {
    map<Source, NodeList> lists;

    for (const auto& entry : received.nodes.entries()) {
        const auto found = received.sources.find(entry.endpoint);
        const auto source = received.sources.end() == found ? Source::Relayed : found->second;

        lists[source].add(entry.endpoint, entry.node_id);
    }

    return lists;
}

// Records each address received names, as learned in the way it says.
void record_received(NodeAddresses& addresses, Database& database,
                     const Recorder::NodesReceived& received) {
    in_transaction(database, [&addresses, &received] {
        for (const auto& [source, list] : lists_by_source(received)) {
            addresses.record_learned(list, source);
        }
    });
    lInfo << "A node list named" << received.nodes.entries().size() << "addresses";
}

// Whether left and right name the same nodes at the same endpoints, in the
// same order.
bool same_entries(const NodeList& left, const NodeList& right) {
    return equal(left.entries().begin(), left.entries().end(), right.entries().begin(),
                 right.entries().end(), [](const NodeList::Entry& one, const NodeList::Entry& other) {
        return one.endpoint == other.endpoint
               && one.node_id.to_string() == other.node_id.to_string();
    });
}

// Whether left and right name the same nodes, at the same endpoints, in the
// same order.
bool same_candidates(const vector<Candidate>& left, const vector<Candidate>& right) {
    return equal(left.begin(), left.end(), right.begin(), right.end(),
                 [](const Candidate& one, const Candidate& other) {
        return one.node_id.to_string() == other.node_id.to_string()
               && one.endpoints == other.endpoints;
    });
}

// The text of each of entries, in order.
template<typename Entry>
vector<string> texts_of(const vector<Entry>& entries) {
    vector<string> texts;

    transform(entries.begin(), entries.end(), back_inserter(texts),
              [](const Entry& entry) { return entry.to_string(); });
    return texts;
}

// Whether left and right name the same identifiers and prefixes, in the
// same order.
bool same_seek(const SeekList& left, const SeekList& right) {
    return texts_of(left.data()) == texts_of(right.data())
           && texts_of(left.search()) == texts_of(right.search());
}

// The endpoint and node id of each entry of list.
set<pair<string, string>> named_in(const NodeList& list) {
    set<pair<string, string>> named;

    transform(list.entries().begin(), list.entries().end(), inserter(named, named.end()),
              [](const NodeList::Entry& entry) {
        return pair(entry.endpoint, entry.node_id.to_string());
    });
    return named;
}

// The entries of after that before does not name, in the order after
// names them.
NodeList newly_named(const NodeList& before, const NodeList& after) {
    const auto named = named_in(before);
    NodeList added;

    for (const auto& entry : after.entries()) {
        if (0 == named.count({entry.endpoint, entry.node_id.to_string()})) {
            added.add(entry.endpoint, entry.node_id);
        }
    }

    return added;
}

// The tables the database holds, made as it opens if they are missing.
string schema() {
    return string(NodeAddresses::schema) + ";\n" + string(ContentUses::schema) + ";\n"
           + string(Sought::schema);
}

// own_endpoints, once they are known to be endpoints. Throws
// invalid_argument for one that is not, as NodeList does.
vector<string> checked_endpoints(const Identifier& own_id, vector<string> own_endpoints) {
    (void)NodeList(own_id, own_endpoints);
    return own_endpoints;
}

// interval, once it is known to be more than zero. Throws invalid_argument,
// naming the setting, when it is not.
Milliseconds checked_interval(Milliseconds interval) {
    if (interval <= Milliseconds::zero()) {
        throw invalid_argument("stats.derive_interval_seconds must be more than zero: "
                               + to_string(interval.count()) + " ms");
    }

    return interval;
}

// Reports what the thread could not handle.
void log_error(const string& text) {
    lErr << text;
}

} // namespace

namespace stats {

Recorder::NodesReceived::operator string() const {
    return to_string(nodes.entries().size()) + " addresses, " + to_string(sources.size())
           + " not relayed";
}

Recorder::ConnectionOpened::operator string() const {
    return node_id.to_string() + " reached at " + endpoint;
}

Recorder::ConnectionFailed::operator string() const {
    return node_id.to_string() + " not reached at " + endpoint;
}

Recorder::NodesUpdated::operator string() const {
    return to_string(nodes.entries().size()) + " entries, " + to_string(added.entries().size())
           + " new";
}

Recorder::CandidatesUpdated::operator string() const {
    return to_string(candidates.size()) + " candidates";
}

Recorder::SearchRequested::operator string() const {
    if (!node) {
        return prefix.to_string();
    }

    return prefix.to_string() + " from " + node->to_string() + ", to be passed on "
           + to_string(relays) + " more times";
}

Recorder::SeekUpdated::operator string() const {
    return to_string(seek.data().size()) + " identifiers, " + to_string(seek.search().size())
           + " prefixes";
}

Recorder::Recorder(const path& database_path, Identifier own_id, vector<string> own_endpoints,
                   Settings settings, Now now)
    : m_own_id(std::move(own_id))
    , m_own_endpoints(checked_endpoints(m_own_id, std::move(own_endpoints)))
    , m_derive_interval(checked_interval(settings.derive_interval))
    , m_database(database_path, schema())
    , m_addresses(m_database, settings.limits, now)
    , m_content_uses(m_database, now)
    , m_sought(m_database, settings.seek_entry_ttl, std::move(now))
    , m_node_list(m_own_id, m_own_endpoints)
    , m_channel({
                    ConnectionFailed::name,
                    ConnectionOpened::name,
                    DataNotFound::name,
                    DataRequested::name,
                    DataStored::name,
                    NodesReceived::name,
                    SearchRequested::name,
                    derive_due,
                },
                [this](const IMessage::Ptr& message) { _handle(message); }, log_error)
    , m_timer(derive_due, m_derive_interval) {
    Channel::post(derive_due, Timer::Tick{derive_due, 0});
}

void Recorder::_handle(const IMessage::Ptr& message) {
    if (const auto* const received = value_of<NodesReceived>(message)) {
        record_received(m_addresses, m_database, *received);
    } else if (const auto* const opened = value_of<ConnectionOpened>(message)) {
        in_transaction(m_database, [this, opened] {
            m_addresses.record_success(opened->node_id, opened->endpoint);
        });
    } else if (const auto* const failed = value_of<ConnectionFailed>(message)) {
        in_transaction(m_database, [this, failed] {
            m_addresses.record_failure(failed->node_id, failed->endpoint);
        });
    } else if (const auto* const requested = value_of<DataRequested>(message)) {
        m_content_uses.record_requested(requested->identifier);
    } else if (const auto* const lacked = value_of<DataNotFound>(message)) {
        if (m_sought.seek(lacked->identifier)) {
            _post_seek_list();
        }
    } else if (const auto* const search = value_of<SearchRequested>(message)) {
        if (m_sought.seek(search->prefix)) {
            _post_seek_list();
        }
    } else if (const auto* const stored = value_of<DataStored>(message)) {
        if (m_sought.found(stored->identifier)) {
            _post_seek_list();
        }
        m_content_uses.record_stored(stored->identifier, stored->size);
    } else if (nullptr != value_of<Timer::Tick>(message)) {
        _derive();
    } else {
        lWarn << "Not an event stats records:" << message->to_string();
    }
}

void Recorder::_derive() {
    auto nodes = m_addresses.node_list(m_own_id, m_own_endpoints);
    auto candidates = m_addresses.candidates(m_own_id);

    if (!m_derived || !same_entries(m_node_list, nodes)) {
        Channel::post(NodesUpdated::name, NodesUpdated{nodes, newly_named(m_node_list, nodes)});
        m_node_list = std::move(nodes);
    }

    if (!m_derived || !same_candidates(m_candidates, candidates)) {
        Channel::post(CandidatesUpdated::name, CandidatesUpdated{candidates});
        m_candidates = std::move(candidates);
    }

    m_sought.forget_expired();
    _post_seek_list();
    m_derived = true;
}

void Recorder::_post_seek_list() {
    auto seek = m_sought.seek_list();

    if (!m_derived || !same_seek(m_seek_list, seek)) {
        Channel::post(SeekUpdated::name, SeekUpdated{seek});
        m_seek_list = std::move(seek);
    }
}

} // namespace stats
