#include "PeerSearch.hpp"

#include <algorithm>
#include <cstddef>
#include <iterator>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "PeerSession.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "logger/Logger.hpp"
#include "msg/Channel.hpp"
#include "protocol/SearchResults.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"
#include "storage/SearchAnswered.hpp"
#include "types/Constants.hpp"

using http::Request;
using http::Response;
using http::ResponseCode;
using msg::Channel;
using peer::PeerSearch;
using peer::PeerSession;
using protocol::SearchResults;
using std::back_inserter;
using std::copy_if;
using std::invalid_argument;
using std::map;
using std::min;
using std::nullopt;
using std::optional;
using std::runtime_error;
using std::scoped_lock;
using std::shared_ptr;
using std::size_t;
using std::sort;
using std::string;
using std::to_string;
using std::vector;
using storage::HashPrefix;
using storage::Identifier;
using storage::SearchAnswered;
using types::search_path_prefix;
using types::search_relays_header;

namespace {

using Clock = PeerSearch::Clock;
using Milliseconds = PeerSearch::Milliseconds;
using Sessions = PeerSearch::Sessions;
// Until when each prefix is held off, by prefix.
using Held = map<string, Clock::time_point>;

// The fewest passes a search makes, as the Python node allows: each after
// the first asks every peer again, which may have found more meanwhile.
constexpr size_t least_passes = 2;

// settings, once checked. Throws invalid_argument, naming the setting, for
// one the Python node refuses.
PeerSearch::Settings checked(const PeerSearch::Settings& settings) {
    if (settings.passes < least_passes) {
        throw invalid_argument("peers.search_passes is less than " + to_string(least_passes));
    }

    if (settings.pass_interval < Milliseconds::zero()) {
        throw invalid_argument("network.retry_after_seconds is less than zero");
    }

    if (settings.hold <= Milliseconds::zero()) {
        throw invalid_argument("peers.broadcast_hold_seconds is not more than zero");
    }

    return settings;
}

// Lets go of the prefixes whose hold is over by now, so that held holds
// only those held lately.
void let_go(Held& held, Clock::time_point now) {
    for (auto entry = held.begin(); entry != held.end();) {
        if (entry->second <= now) {
            entry = held.erase(entry);
        } else {
            ++entry;
        }
    }
}

// The sessions of sessions to ask for prefix: those open, but the one with
// asker, those whose node ids best match prefix first, and those that match
// it alike in the order of their ids, so that the same peers always rank
// alike.
Sessions ranked(const Sessions& sessions, const HashPrefix& prefix,
                const optional<Identifier>& asker) {
    const auto asker_id = asker ? asker->to_string() : string();
    Sessions asked;

    copy_if(sessions.begin(), sessions.end(), back_inserter(asked),
            [&asker_id](const shared_ptr<PeerSession>& session) {
        return session && session->is_open() && session->node_id().to_string() != asker_id;
    });
    sort(asked.begin(), asked.end(),
         [&prefix](const shared_ptr<PeerSession>& one, const shared_ptr<PeerSession>& other) {
        const auto one_bits = prefix.matching_bits(one->node_id());
        const auto other_bits = prefix.matching_bits(other->node_id());

        if (one_bits != other_bits) {
            return one_bits > other_bits;
        }

        return one->node_id().to_string() < other->node_id().to_string();
    });
    return asked;
}

// The search for prefix, telling the peer it may pass it on relays times
// more.
Request search_request(const HashPrefix& prefix, size_t relays) {
    Request request;

    request.set_method("GET");
    request.set_uri(string(search_path_prefix) + prefix.to_string());
    request.headers().set(string(search_relays_header), to_string(relays));
    return request;
}

// What session's peer answered request with: nothing when it could not be
// asked, or its answer could not be read or shown to be its own, which
// leaves it out of this pass.
optional<Response> answer_from(PeerSession& session, const Request& request) {
    try {
        auto answers = session.exchange({request});

        return std::move(answers.at(0));
    } catch (const runtime_error& error) {
        lDebug << "Could not ask" << session.node_id().to_string() << "to search:" << error;
    } catch (const invalid_argument& error) {
        lDebug << "Could not read the answer of" << session.node_id().to_string()
               << "to a search:" << error;
    }

    return nullopt;
}

// The identifiers answer names, best first: nothing for an answer that is
// not 200 OK, or that is not a search's answer decompressing to no more than
// list_bytes, from the peer node_id.
optional<vector<Identifier>> results_in(const Response& answer, const Identifier& node_id,
                                        size_t list_bytes) {
    if (answer.code() != ResponseCode::Status::Ok) {
        lDebug << node_id.to_string() << "answered a search with status" << answer.status_code();
        return nullopt;
    }

    try {
        return SearchResults::parse(answer.body(), list_bytes).results();
    } catch (const invalid_argument& error) {
        lDebug << "Leaving out the answer of" << node_id.to_string() << "to a search:" << error;
        return nullopt;
    }
}

// Asks session's peer for prefix, telling it it may pass the search on
// relays times more, and posts what it answered, unless it named nothing.
void ask(PeerSession& session, const HashPrefix& prefix, size_t relays, size_t list_bytes) {
    const auto answer = answer_from(session, search_request(prefix, relays));

    if (!answer) {
        return;
    }

    auto results = results_in(*answer, session.node_id(), list_bytes);

    if (!results || results->empty()) {
        return;
    }

    Channel::post(SearchAnswered::name,
                  SearchAnswered{prefix, session.node_id(), std::move(*results)});
}

} // namespace

namespace peer {

PeerSearch::PeerSearch(const Settings& settings, ClockFunction clock)
    : m_settings(checked(settings))
    , m_clock(std::move(clock)) {
    if (!m_clock) {
        throw invalid_argument("A search of the peers needs the time");
    }
}

bool PeerSearch::start(const HashPrefix& prefix, const optional<Asker>& asker) {
    const auto& key = prefix.to_string();
    optional<Identifier> asked_by;
    auto relays = m_settings.relays;

    if (asker) {
        relays = min(asker->relays, relays);

        if (0 == relays) {
            lDebug << "Not passing on the search of" << asker->node.to_string() << "for" << key;
            return false;
        }

        --relays;
        asked_by = asker->node;
    }

    const scoped_lock lock(m_mutex);
    const auto now = m_clock();

    let_go(m_held, now);

    if (0 != m_searches.count(key) || 0 != m_held.count(key)) {
        lDebug << "Not searching the peers for" << key << "again so soon";
        return false;
    }

    m_searches.emplace(key, Search{prefix, std::move(asked_by), relays, 1, now});
    lDebug << "Searching the peers for" << key;
    return true;
}

optional<PeerSearch::Clock::time_point> PeerSearch::make_passes(const Sessions& sessions) {
    while (const auto pass = _take_due()) {
        for (const auto& session : ranked(sessions, pass->prefix, pass->asker)) {
            ask(*session, pass->prefix, pass->relays, m_settings.list_bytes);
        }

        _made(*pass);
    }

    return _next_due();
}

// Takes the pass due soonest of those due now, so that no other thread
// makes it too: nothing when none is due.
optional<PeerSearch::Pass> PeerSearch::_take_due() {
    const scoped_lock lock(m_mutex);
    const auto now = m_clock();
    auto soonest = Clock::time_point::max();
    Search* taken = nullptr;

    for (auto& entry : m_searches) {
        const auto due = entry.second.due.value_or(Clock::time_point::max());

        if (due <= now && due < soonest) {
            soonest = due;
            taken = &entry.second;
        }
    }

    if (nullptr == taken) {
        return nullopt;
    }

    taken->due.reset();
    return Pass{taken->prefix, taken->asker, taken->relays};
}

// Readies the next pass of the search pass was made for, or after its last,
// ends it, holding off another for its prefix. Nothing else ends a search, so
// it is still under way.
void PeerSearch::_made(const Pass& pass) {
    const auto key = pass.prefix.to_string();
    const scoped_lock lock(m_mutex);
    auto& search = m_searches.at(key);

    if (search.pass < m_settings.passes) {
        ++search.pass;
        search.due = m_clock() + m_settings.pass_interval;
        return;
    }

    m_searches.erase(key);
    m_held.insert_or_assign(key, m_clock() + m_settings.hold);
    lDebug << "Searched the peers for" << key;
}

// When the next pass of a search is due: nothing when none waits for one.
optional<PeerSearch::Clock::time_point> PeerSearch::_next_due() {
    const scoped_lock lock(m_mutex);
    optional<Clock::time_point> next;

    for (const auto& entry : m_searches) {
        const auto& due = entry.second.due;

        if (due && (!next || due < next)) {
            next = due;
        }
    }

    return next;
}

} // namespace peer
