#include "Recorder.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <iterator>
#include <map>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include "ContentUses.hpp"
#include "NodeAddresses.hpp"
#include "Sought.hpp"
#include "db/Database.hpp"
#include "eviction/Evictor.hpp"
#include "eviction/Scorer.hpp"
#include "logger/Logger.hpp"
#include "msg/Channel.hpp"
#include "msg/IMessage.hpp"
#include "msg/Message.hpp"
#include "msg/Timer.hpp"
#include "protocol/NodeList.hpp"
#include "protocol/SeekList.hpp"
#include "storage/DataDeleted.hpp"
#include "storage/DataNotFound.hpp"
#include "storage/DataRequested.hpp"
#include "storage/DataStored.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"

using db::Database;
using eviction::Evictor;
using eviction::Scorer;
using msg::Channel;
using msg::IMessage;
using msg::Message;
using msg::Timer;
using msg::value_of;
using protocol::NodeList;
using protocol::SeekList;
using stats::ContentUses;
using stats::NodeAddresses;
using stats::Recorder;
using stats::Sought;
using std::back_inserter;
using std::copy_if;
using std::equal;
using std::inserter;
using std::invalid_argument;
using std::map;
using std::max;
using std::optional;
using std::pair;
using std::set;
using std::size_t;
using std::stable_sort;
using std::string;
using std::tie;
using std::to_string;
using std::transform;
using std::uintmax_t;
using std::vector;
using std::filesystem::path;
using storage::DataDeleted;
using storage::DataNotFound;
using storage::DataRequested;
using storage::DataStored;
using storage::HashPrefix;
using storage::Identifier;

// The thread takes these events, which others post from other files, so
// nothing here would make their messages' virtual to_string(), which
// value_of() casts to.
template class msg::Message<DataDeleted>;
template class msg::Message<DataNotFound>;
template class msg::Message<DataRequested>;
template class msg::Message<DataStored>;
template class msg::Message<Evictor::CandidatesRequested>;
template class msg::Message<Recorder::ConnectionFailed>;
template class msg::Message<Recorder::ConnectionOpened>;
template class msg::Message<Recorder::NodesReceived>;
template class msg::Message<Recorder::SearchRequested>;

namespace {

using Candidate = NodeAddresses::Candidate;
using CandidatesRequested = Evictor::CandidatesRequested;
using Clock = Recorder::Clock;
using Held = ContentUses::Held;
using Milliseconds = Recorder::Milliseconds;
using Source = NodeAddresses::Source;
using Uses = ContentUses::Uses;

// An object held, and how soon it goes.
struct Scored {
    double score = 0.0;
    Held held;
};

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

// most, once it is known to be at least 1. Throws invalid_argument, naming
// the setting, when it is not.
size_t checked_max_candidates(size_t most) {
    if (most < 1) {
        throw invalid_argument("storage.max_candidates must be at least 1");
    }

    return most;
}

// When uses says its object was last used: asked for or stored, whichever
// was later.
optional<Clock::time_point> last_used(const Uses& uses) {
    if (!uses.last_requested || !uses.last_stored) {
        return uses.last_requested ? uses.last_requested : uses.last_stored;
    }

    return max(*uses.last_requested, *uses.last_stored);
}

// The extremes of held, at now, that each of it is scored against, with
// node the node id's hash.
Scorer::Extremes extremes_of(const vector<Held>& held, const HashPrefix& node,
                             Clock::time_point now) {
    Scorer::Extremes extremes;

    for (const auto& object : held) {
        const auto used = last_used(object.uses);

        if (used) {
            extremes.longest_unused = max(extremes.longest_unused, now - *used);
        }

        extremes.most_requests = max(extremes.most_requests, object.uses.requests);
        extremes.most_matching_bits =
            max(extremes.most_matching_bits, node.matching_bits(object.identifier));
    }

    return extremes;
}

// Whether left goes before right: it scores higher, or as high with a hash,
// then an algorithm, that sorts first.
bool goes_before(const Scored& left, const Scored& right) {
    if (left.score != right.score) {
        return left.score > right.score;
    }

    const auto& one = left.held.identifier;
    const auto& other = right.held.identifier;

    return tie(one.hash(), one.algorithm()) < tie(other.hash(), other.algorithm());
}

// held, but for the object identifier names.
vector<Held> but_for(const vector<Held>& held, const Identifier& identifier) {
    const auto name = identifier.to_string();
    vector<Held> others;

    copy_if(held.begin(), held.end(), back_inserter(others),
            [&name](const Held& object) { return object.identifier.to_string() != name; });
    return others;
}

// What held holds, but for own_id's key, in the order to let it go, at now.
vector<Scored> ranked(const vector<Held>& held, const Identifier& own_id, Clock::time_point now) {
    const auto others = but_for(held, own_id);
    const Scorer scoring(own_id, now, extremes_of(others, HashPrefix::parse(own_id.hash()), now));
    vector<Scored> scored;

    transform(others.begin(), others.end(), back_inserter(scored), [&scoring](const Held& object) {
        const auto& uses = object.uses;
        const auto score = scoring.of(object.identifier, uses.size.value_or(0), uses.requests,
                                      last_used(uses));

        return Scored{score, object};
    });
    stable_sort(scored.begin(), scored.end(), goes_before);
    return scored;
}

// The objects held to let go of first, at now, but for own_id's key and
// those asked names: enough to free the bytes asked for, or most.
Evictor::Candidates candidates_for(const ContentUses& content_uses, const Identifier& own_id,
                                   Clock::time_point now, const CandidatesRequested& asked,
                                   size_t most) {
    set<string> excluded;
    Evictor::Candidates candidates;
    uintmax_t covered = 0;

    transform(asked.exclude.begin(), asked.exclude.end(), inserter(excluded, excluded.end()),
              [](const Identifier& identifier) { return identifier.to_string(); });

    for (const auto& [score, object] : ranked(content_uses.held(), own_id, now)) {
        if (candidates.objects.size() >= most) {
            break;
        }

        if (0 != excluded.count(object.identifier.to_string())) {
            continue;
        }

        candidates.objects.push_back({object.identifier, object.uses.size.value_or(0)});
        covered += candidates.objects.back().size;

        if (covered >= asked.bytes) {
            break;
        }
    }

    return candidates;
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
                   Settings settings, ClockFunction clock)
    : m_own_id(std::move(own_id))
    , m_own_endpoints(checked_endpoints(m_own_id, std::move(own_endpoints)))
    , m_derive_interval(checked_interval(settings.derive_interval))
    , m_max_candidates(checked_max_candidates(settings.max_candidates))
    , m_clock(clock)
    , m_database(database_path, schema())
    , m_addresses(m_database, settings.limits, clock)
    , m_content_uses(m_database, clock)
    , m_sought(m_database, settings.seek_entry_ttl, std::move(clock))
    , m_node_list(m_own_id, m_own_endpoints)
    , m_channel({
                    CandidatesRequested::name,
                    ConnectionFailed::name,
                    ConnectionOpened::name,
                    DataDeleted::name,
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
    } else if (const auto* const deleted = value_of<DataDeleted>(message)) {
        m_content_uses.record_deleted(deleted->identifier);
    } else if (const auto* const asked = value_of<CandidatesRequested>(message)) {
        Channel::post(Evictor::Candidates::name, candidates_for(m_content_uses, m_own_id, m_clock(),
                                                                *asked, m_max_candidates));
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
