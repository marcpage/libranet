#include "Evictor.hpp"

#include <algorithm>
#include <cstdint>
#include <exception>
#include <filesystem>
#include <functional>
#include <future>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "logger/Logger.hpp"
#include "msg/Channel.hpp"
#include "msg/IMessage.hpp"
#include "msg/Message.hpp"
#include "storage/DataDeleted.hpp"
#include "storage/DataStored.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"

using msg::Channel;
using msg::IMessage;
using msg::value_of;
using std::any_of;
using std::exception;
using std::function;
using std::future;
using std::future_status;
using std::invalid_argument;
using std::map;
using std::max;
using std::min;
using std::nullopt;
using std::optional;
using std::string;
using std::to_string;
using std::uintmax_t;
using std::vector;
using std::filesystem::path;
using storage::DataDeleted;
using storage::DataStored;
using storage::IDataSource;
using storage::Identifier;
using types::Megabyte;

// The thread takes these events, which others post from other files, so
// nothing here would make their messages' virtual to_string(), which
// value_of() casts to.
template class msg::Message<DataStored>;
template class msg::Message<eviction::Evictor::Candidates>;

namespace {

using Clock = eviction::Evictor::Clock;
using HandOff = function<optional<Identifier>(const Identifier&)>;
using Held = eviction::Evictor::Held;
using Milliseconds = eviction::Evictor::Milliseconds;
using Peers = eviction::Evictor::Peers;
using Settings = eviction::Evictor::Settings;

// what, once it is known to be something: a store or a function. Throws
// invalid_argument, saying what it is for, when it is not.
template<typename Needed>
Needed present(Needed what, const string& purpose) {
    if (!what) {
        throw invalid_argument("An evictor needs " + purpose);
    }

    return what;
}

// peers, once each of its functions is known to be one. Throws
// invalid_argument when one is not.
Peers checked_peers(Peers peers) {
    peers.hand_off = present(std::move(peers.hand_off), "a way to hand content off");
    peers.connected = present(std::move(peers.connected), "the nodes connected");
    return peers;
}

// settings, once each is known to be one it can use. Throws
// invalid_argument, naming the setting, when one is not.
Settings checked_settings(const Settings& settings) {
    if (settings.max_hand_offs < 1) {
        throw invalid_argument("storage.max_hand_offs must be at least 1");
    }

    if (settings.stored_grace < Milliseconds::zero()) {
        throw invalid_argument("storage.stored_grace_seconds must not be less than zero: "
                               + to_string(settings.stored_grace.count()) + " ms");
    }

    if (settings.candidates_timeout <= Milliseconds::zero()) {
        throw invalid_argument("storage.candidates_timeout_seconds must be more than zero: "
                               + to_string(settings.candidates_timeout.count()) + " ms");
    }

    return settings;
}

// How far short of each limit settings have eviction aim: as many objects as
// large as one may be (HighLevelDesign §4.3) as may be handed off at once.
uintmax_t headroom_of(const Settings& settings) {
    return static_cast<uintmax_t>(settings.max_hand_offs) * Megabyte;
}

// How far amount is beyond limit; 0 when it is within it.
uintmax_t beyond(uintmax_t amount, uintmax_t limit) {
    return amount > limit ? amount - limit : 0;
}

// The bytes the objects being handed off take.
uintmax_t bytes_of(const map<string, Held>& handing_off) {
    uintmax_t bytes = 0;

    for (const auto& [name, held] : handing_off) {
        bytes += held.size;
    }

    return bytes;
}

// Whether identifiers names identifier.
bool names(const vector<Identifier>& identifiers, const Identifier& identifier) {
    const auto text = identifier.to_string();

    return any_of(identifiers.begin(), identifiers.end(),
                  [&text](const Identifier& named) { return named.to_string() == text; });
}

// Forgets what stored names that was stored at since or before, as its
// grace is over.
void forget_before(map<string, Clock::time_point>& stored, Clock::time_point since) {
    for (auto entry = stored.begin(); entry != stored.end();) {
        if (entry->second <= since) {
            entry = stored.erase(entry);
        } else {
            ++entry;
        }
    }
}

// The node id of the peer hand_off says took identifier, if one did. A
// hand-off that throws took it to none, and is logged, as the thread it
// runs on has no one else to tell.
optional<Identifier> taker_of(const HandOff& hand_off, const Identifier& identifier) {
    try {
        return hand_off(identifier);
    } catch (const exception& error) {
        lWarn << "Could not hand off" << identifier.to_string() + ":" << error.what();
    }

    return nullopt;
}

// Reports what the thread could not handle.
void log_error(const string& text) {
    lErr << text;
}

} // namespace

namespace eviction {

Evictor::CandidatesRequested::operator string() const {
    return to_string(bytes) + " bytes, " + to_string(exclude.size()) + " left out";
}

Evictor::Candidates::operator string() const {
    return to_string(objects.size()) + " objects";
}

Evictor::HandedOff::operator string() const {
    return held.identifier.to_string() + " taken by "
           + (holder ? holder->to_string() : string("no peer"));
}

Evictor::FreeSpace Evictor::free_space_in(path directory) {
    // Named in full: with space() brought in by a using-declaration,
    // cppcheck takes the variable Json's serializer names space for it, and
    // reports that variable unread.
    return [directory = std::move(directory)] {
        return std::filesystem::space(directory).available;
    };
}

Evictor::Evictor(Identifier own_id, IDataSource::Ptr store, Peers peers, FreeSpace free_space,
                 Settings settings, ClockFunction clock)
    : m_own_id(std::move(own_id))
    , m_store(present(std::move(store), "the store that holds what it lets go of"))
    , m_peers(checked_peers(std::move(peers)))
    , m_free_space(present(std::move(free_space), "a way to read the bytes free"))
    , m_settings(checked_settings(settings))
    , m_clock(present(std::move(clock), "a clock"))
    , m_held(m_settings.max_storage_bytes ? m_store->size() : 0)
    , m_channel({DataStored::name, Candidates::name, HandedOff::name},
                [this](const IMessage::Ptr& message) { _handle(message); }, log_error) {}

Evictor::~Evictor() {
    m_channel.stop();

    // The thread has stopped, so no hand-off starts now, and those started
    // are all there are.
    for (const auto& job : m_jobs) {
        job.wait();
    }
}

// Handles message, once what was stored before the grace is forgotten.
void Evictor::_handle(const IMessage::Ptr& message) {
    forget_before(m_stored, m_clock() - m_settings.stored_grace);

    if (const auto* const stored = value_of<DataStored>(message)) {
        _stored(*stored);
    } else if (const auto* const candidates = value_of<Candidates>(message)) {
        _take(*candidates);
    } else if (const auto* const handed_off = value_of<HandedOff>(message)) {
        _handed_off(*handed_off);
    } else {
        lWarn << "Not an event eviction takes:" << message->to_string();
    }
}

// Keeps stored for its grace, counts it, and checks storage, with new
// hand-offs, and a new list, allowed again.
void Evictor::_stored(const DataStored& stored) {
    m_stored[stored.identifier.to_string()] = m_clock();

    if (m_settings.max_storage_bytes) {
        m_held += stored.size;
    }

    m_held_off = false;
    m_may_ask = true;
    _evict();
}

// Works through candidates, the list asked for. One that is empty leaves
// nothing to let go of until the next data.stored.
void Evictor::_take(const Candidates& candidates) {
    m_asked_at.reset();
    m_candidates.assign(candidates.objects.begin(), candidates.objects.end());

    if (!m_candidates.empty()) {
        _evict();
        return;
    }

    const auto over = _excess(0);

    if (over > 0 && m_handing_off.empty()) {
        lWarn << "Storage is" << over << "bytes over its limits, with nothing left to let go of";
    } else {
        lDebug << "Nothing more to let go of for now";
    }
}

// Deletes what a peer took, unless it is to be kept now, and carries on.
// What no peer took holds off new hand-offs until the next data.stored.
void Evictor::_handed_off(const HandedOff& handed_off) {
    const auto& held = handed_off.held;

    m_handing_off.erase(held.identifier.to_string());

    if (!handed_off.holder) {
        lInfo << "No peer took" << held.identifier.to_string() + ", so it is kept for now";
        m_held_off = true;
        return;
    }

    if (_passes_over(held.identifier, _kept())) {
        lInfo << held.identifier.to_string() << "is to be kept now, so it is not deleted";
    } else {
        _delete(held, *handed_off.holder);
    }

    _evict();
}

// Starts hand-offs from the list, asking for one when it runs out, until
// those under way would bring storage the headroom within its limits once
// they are deleted, or as many are under way as may be.
void Evictor::_evict() {
    const auto excess = _excess(headroom_of(m_settings));

    if (0 == excess) {
        m_candidates.clear();
        return;
    }

    if (m_held_off) {
        return;
    }

    const auto kept = _kept();
    auto freeing = bytes_of(m_handing_off);

    while (freeing < excess && m_handing_off.size() < m_settings.max_hand_offs) {
        if (m_candidates.empty()) {
            _ask(excess - freeing, kept);
            return;
        }

        const auto held = m_candidates.front();

        m_candidates.pop_front();

        if (_passes_over(held.identifier, kept)) {
            continue;
        }

        if (!m_store->has(held.identifier)) {
            lInfo << held.identifier.to_string() << "is no longer held";
            _forget(held);
            continue;
        }

        _hand_off(held);
        freeing += held.size;
    }
}

// Asks for the objects to let go of first, enough to free bytes, leaving
// out kept and what is being handed off or is within its grace: unless no
// list may be asked for since the last, or the last asked for is not yet
// overdue.
void Evictor::_ask(uintmax_t bytes, const Identifiers& kept) {
    const auto now = m_clock();

    if (!m_may_ask || (m_asked_at && now - *m_asked_at < m_settings.candidates_timeout)) {
        return;
    }

    CandidatesRequested request{bytes, kept};

    for (const auto& [name, held] : m_handing_off) {
        request.exclude.push_back(held.identifier);
    }

    for (const auto& [name, stored_at] : m_stored) {
        request.exclude.push_back(Identifier::parse(name));
    }

    m_may_ask = false;
    m_asked_at = now;
    Channel::post(CandidatesRequested::name, std::move(request));
    lDebug << "Asked for content to free" << bytes << "bytes";
}

// Offers held to the peers on a thread of its own, which posts what came of
// it to this one, and lets go of the hand-offs that have ended.
void Evictor::_hand_off(const Held& held) {
    m_jobs.remove_if([](const future<void>& job) {
        return future_status::ready == job.wait_for(Milliseconds::zero());
    });
    // Captured as copies of their own, so that moving the job moves them.
    m_jobs.push_back(std::async(std::launch::async,
                                [hand_off = m_peers.hand_off, to_offer = held] {
        Channel::post(HandedOff::name,
                      HandedOff{to_offer, taker_of(hand_off, to_offer.identifier)});
    }));
    m_handing_off.emplace(held.identifier.to_string(), held);
    lDebug << "Handing off" << held.identifier.to_string();
}

// Deletes held, which holder took.
void Evictor::_delete(const Held& held, const Identifier& holder) {
    if (m_store->remove(held.identifier)) {
        lInfo << "Deleted" << held.identifier.to_string() + ", which" << holder.to_string()
              << "took";
    } else {
        lInfo << held.identifier.to_string() << "was gone already when" << holder.to_string()
              << "took it";
    }

    _forget(held);
}

// Counts held as no longer held, and posts it, so that it is not listed
// again; and allows a new list, as there is less to let go of.
void Evictor::_forget(const Held& held) {
    m_held -= min(m_held, static_cast<uintmax_t>(held.size));
    m_may_ask = true;
    Channel::post(DataDeleted::name, DataDeleted{held.identifier, held.size});
}

// Whether identifier is to be kept for now: it is one of kept, is being
// handed off, or was stored within its grace.
bool Evictor::_passes_over(const Identifier& identifier, const Identifiers& kept) const {
    const auto name = identifier.to_string();

    return names(kept, identifier) || 0 != m_handing_off.count(name) || 0 != m_stored.count(name);
}

// The keys signatures are checked against now: those of the nodes connected
// either way, and this node's own.
Evictor::Identifiers Evictor::_kept() const {
    auto kept = m_peers.connected();

    kept.push_back(m_own_id);
    return kept;
}

// The bytes to let go of for storage to be headroom within each of its
// limits; 0 when it is.
uintmax_t Evictor::_excess(uintmax_t headroom) const {
    uintmax_t excess = 0;

    if (m_settings.min_free_bytes > 0) {
        excess = beyond(m_settings.min_free_bytes + headroom, m_free_space());
    }

    if (m_settings.max_storage_bytes) {
        excess = max(excess, beyond(m_held + headroom, *m_settings.max_storage_bytes));
    }

    return excess;
}

} // namespace eviction
