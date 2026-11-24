#include "Fetch.hpp"

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <exception>
#include <functional>
#include <map>
#include <mutex>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "PeerSession.hpp"
#include "http/Headers.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "logger/Logger.hpp"
#include "msg/Channel.hpp"
#include "storage/DataStored.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Converter.hpp"

using http::Headers;
using http::Request;
using http::Response;
using http::ResponseCode;
using msg::Channel;
using peer::Fetch;
using peer::PeerSession;
using std::function;
using std::invalid_argument;
using std::map;
using std::nullopt;
using std::optional;
using std::set;
using std::size_t;
using std::string;
using std::string_view;
using std::to_string;
using std::vector;
using storage::DataStored;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;

namespace {

using Clock = Fetch::Clock;
using Milliseconds = Fetch::Milliseconds;
using Session = Fetch::Session;
// Until when each is held off, by identifier or node id.
using Until = map<string, Clock::time_point>;

// The longest a peer's Retry-After holds it off: a search is over well
// within it, and a longer one could overflow a time.
constexpr std::chrono::seconds longest_retry_after = std::chrono::hours(24);

// Calls end as it goes, however the scope it is in ends.
class AtEnd {
public:
    explicit AtEnd(function<void()> end)
        : m_end(std::move(end)) {}
    // A destructor may not throw, and what end failed to do cannot be done
    // after, so then nothing is safe but to end the program.
    ~AtEnd() {
        try {
            m_end();
        } catch (const std::exception& error) {
            lFatal << "Could not end a search:" << error.what();
        } catch (...) {
            std::terminate();
        }
    }
    AtEnd(const AtEnd&) = delete;
    AtEnd& operator=(const AtEnd&) = delete;
    AtEnd(AtEnd&&) = delete;
    AtEnd& operator=(AtEnd&&) = delete;

private:
    function<void()> m_end;
};

// settings, once checked, as the Python node checks them.
Fetch::Settings checked(const Fetch::Settings& settings) {
    if (settings.passes < 2) {
        throw invalid_argument("peers.search_passes is less than 2: "
                               + to_string(settings.passes));
    }

    if (settings.retry_after < Milliseconds::zero()) {
        throw invalid_argument("network.retry_after_seconds is less than zero");
    }

    // In floating point, so that many passes cannot overflow it.
    const auto passes_after_first = static_cast<double>(settings.passes - 1);
    const auto longest_search = settings.retry_after * passes_after_first;

    if (settings.failed_search_hold <= longest_search) {
        throw invalid_argument("peers.failed_search_hold_seconds is not longer than a search may "
                               "last: peers.search_passes less one, times "
                               "network.retry_after_seconds");
    }

    return settings;
}

// Lets go of those whose hold is over by now, so that until holds only
// those held lately.
void let_go(Until& until, Clock::time_point now) {
    for (auto entry = until.begin(); entry != until.end();) {
        if (entry->second <= now) {
            entry = until.erase(entry);
        } else {
            ++entry;
        }
    }
}

// The sessions of peers still open, those whose node ids best match
// identifier first, and those that match alike in the order given.
vector<Session> ranked(vector<Session> peers, const Identifier& identifier) {
    const auto is_closed = [](const Session& session) { return !session || !session->is_open(); };

    peers.erase(std::remove_if(peers.begin(), peers.end(), is_closed), peers.end());
    std::stable_sort(peers.begin(), peers.end(),
                     [&identifier](const Session& left, const Session& right) {
        return left->matching_bits(identifier) > right->matching_bits(identifier);
    });
    return peers;
}

// How long answer's Retry-After asks it be left, when it gives a number of
// seconds (HttpApi §5.2), and no longer than longest_retry_after. Nothing
// for none, or for the HTTP date RFC 9110 §10.2.3 also allows, as the
// Python node reads it.
optional<Milliseconds> retry_after_of(const Response& answer) {
    constexpr string_view digits = "0123456789";
    constexpr std::uint64_t decimal_base = 10;
    const auto header = answer.headers().get("Retry-After");
    const auto text = header ? Headers::trim_whitespace(*header) : string_view();

    if (text.empty() || string_view::npos != text.find_first_not_of(digits)) {
        return nullopt;
    }

    std::uint64_t seconds = 0;

    for (const char digit : text) {
        seconds = (seconds * decimal_base) + digits.find(digit);

        if (seconds >= static_cast<std::uint64_t>(longest_retry_after.count())) {
            return longest_retry_after;
        }
    }

    return std::chrono::seconds(seconds);
}

// A GET of identifier's content (HttpApi §5).
Request request_for(const Identifier& identifier) {
    Request request;

    request.set_method("GET");
    request.set_uri("/data/" + identifier.to_string());
    return request;
}

// Logs that session's node could not be asked for identifier, and why.
void report_unasked(const PeerSession& session, const Identifier& identifier,
                    const std::exception& error) {
    lDebug << "Could not ask" << session.address().to_string() << "for"
           << identifier.to_string() << ":" << error.what();
}

// The answer of session's node to a request for identifier's content.
// Nothing when it could not be asked: its connection closed or failed, or
// its answer could not be read or shown to come from it, each of which ends
// the session.
optional<Response> answer_of(PeerSession& session, const Identifier& identifier) {
    try {
        auto answers = session.exchange({request_for(identifier)});

        return std::move(answers.at(0));
    } catch (const std::runtime_error& error) {
        report_unasked(session, identifier, error);
    } catch (const invalid_argument& error) {
        report_unasked(session, identifier, error);
    }

    return nullopt;
}

// Whether answer, from session's node, is identifier's content, as it is or
// once decompressed to no more than limit bytes. When it is, store keeps it
// as it came, and it is posted as stored from that node, unless store held
// it already.
bool kept(const IDataSource& store, const Identifier& identifier, const PeerSession& session,
          const Response& answer, size_t limit) {
    if (ResponseCode::Status::Ok != answer.code().status()) {
        return false;
    }

    const auto body = Converter::bytes_of(answer.body());

    if (!identifier.content_of(body, limit)) {
        lInfo << session.address().to_string() << "sent what is not" << identifier.to_string();
        return false;
    }

    if (!store.has(identifier)) {
        store.put(identifier, body);
        Channel::post(DataStored::name, DataStored{identifier, session.node_id(), body.size()});
    }

    return true;
}

// A search for one object, and how far it has gone (High-Level Design §4.7).
// Each pass asks each peer in turn, best match first, but one that did not
// send the object until the Retry-After it gave has passed, and the next
// pass begins once each may be asked again, but waits no longer than this
// node's own Retry-After.
class Search {
public:
    Search(Identifier identifier, IDataSource::Ptr store, const Fetch::Settings& settings,
           Fetch::ClockFunction clock)
        : m_identifier(std::move(identifier))
        , m_store(std::move(store))
        , m_settings(settings)
        , m_clock(std::move(clock)) {}

    // The peer this pass asks next, of peers, best first; nothing once it
    // has asked or passed over each. One not due yet is passed over.
    [[nodiscard]] Session next_peer(const vector<Session>& peers, Clock::time_point now);
    // Asks session's node for the object; whether it sent it, now kept.
    [[nodiscard]] bool ask(PeerSession& session);
    [[nodiscard]] bool is_last_pass() const noexcept;
    // Starts the next pass over peers, and gives when it may ask its first
    // peer.
    [[nodiscard]] Clock::time_point start_next_pass(const vector<Session>& peers,
                                                    Clock::time_point now);
    // Whether it has asked any peer, whose own search may start it again.
    [[nodiscard]] bool has_asked() const noexcept;

private:
    Identifier m_identifier;
    IDataSource::Ptr m_store;
    Fetch::Settings m_settings;
    Fetch::ClockFunction m_clock;
    size_t m_pass{1}; // Counting from 1
    size_t m_asks{0};
    set<string> m_passed; // This pass's, asked or passed over, by node id
    Until m_due;          // When each may be asked again, by node id
};

Session Search::next_peer(const vector<Session>& peers, Clock::time_point now) {
    for (const auto& session : peers) {
        auto node_id = session->node_id().to_string();
        const auto due = m_due.find(node_id);

        if (!m_passed.insert(std::move(node_id)).second) {
            continue;
        }

        if (due == m_due.end() || due->second <= now) {
            ++m_asks;
            return session;
        }
    }

    return nullptr;
}

bool Search::ask(PeerSession& session) {
    const auto answer = answer_of(session, m_identifier);

    if (answer && kept(*m_store, m_identifier, session, *answer, m_settings.content_bytes)) {
        return true;
    }

    const auto retry_after = answer ? retry_after_of(*answer) : nullopt;

    m_due[session.node_id().to_string()] = m_clock() + retry_after.value_or(m_settings.retry_after);
    return false;
}

bool Search::is_last_pass() const noexcept {
    return m_pass >= m_settings.passes;
}

Clock::time_point Search::start_next_pass(const vector<Session>& peers, Clock::time_point now) {
    auto resume = now;

    ++m_pass;
    m_passed.clear();

    for (const auto& session : peers) {
        const auto due = m_due.find(session->node_id().to_string());

        if (due != m_due.end()) {
            resume = std::max(resume, due->second);
        }
    }

    return std::min(resume, now + m_settings.retry_after);
}

bool Search::has_asked() const noexcept {
    return m_asks > 0;
}

} // namespace

namespace peer {

Fetch::Fetch(IDataSource::Ptr store, GetPeers get_peers, const Settings& settings,
             ClockFunction clock)
    : m_store(std::move(store))
    , m_get_peers(std::move(get_peers))
    , m_settings(checked(settings))
    , m_clock(std::move(clock)) {
    if (!m_store) {
        throw invalid_argument("A fetch needs a store to keep what it finds");
    }

    if (!m_get_peers) {
        throw invalid_argument("A fetch needs the peers to ask");
    }

    if (!m_clock) {
        throw invalid_argument("A fetch needs the time");
    }
}

Fetch::Outcome Fetch::fetch(const Identifier& identifier) {
    if (const auto refused = _start(identifier)) {
        return *refused;
    }

    // However the search ends, a new one may start after it.
    const AtEnd ended([this, &identifier] { _end(identifier); });

    return _search(identifier);
}

bool Fetch::is_held_off(const Identifier& identifier) const {
    const std::scoped_lock lock(m_mutex);
    const auto held = m_held.find(identifier.to_string());

    return held != m_held.end() && m_clock() < held->second;
}

void Fetch::stop() {
    {
        const std::scoped_lock lock(m_mutex);

        m_stopped = true;
    }

    m_stopping.notify_all();
}

// Marks a search for identifier under way, and gives nothing; or, when none
// may start, why not.
optional<Fetch::Outcome> Fetch::_start(const Identifier& identifier) {
    const std::scoped_lock lock(m_mutex);
    auto key = identifier.to_string();

    if (m_stopped) {
        return Outcome::Stopped;
    }

    let_go(m_held, m_clock());

    if (0 != m_held.count(key)) {
        lDebug << "Not searching for" << key << "yet: the last search found nothing";
        return Outcome::HeldOff;
    }

    if (!m_under_way.insert(std::move(key)).second) {
        return Outcome::Searching;
    }

    return nullopt;
}

// Asks the peers for identifier's content, pass after pass, until one sends
// it, the node comes to hold it, the last pass ends, or stop() is called.
Fetch::Outcome Fetch::_search(const Identifier& identifier) {
    Search search(identifier, m_store, m_settings, m_clock);

    while (!_is_stopped()) {
        if (m_store->has(identifier)) {
            return Outcome::Found;
        }

        const auto peers = ranked(m_get_peers(), identifier);
        const auto now = m_clock();

        if (const auto session = search.next_peer(peers, now)) {
            if (search.ask(*session)) {
                return Outcome::Found;
            }

            continue;
        }

        if (search.is_last_pass()) {
            // One that asked no peer set off no search of theirs, which
            // could start it again.
            if (search.has_asked()) {
                _hold_off(identifier);
            }

            return Outcome::NotFound;
        }

        _wait_until(search.start_next_pass(peers, now));
    }

    return Outcome::Stopped;
}

bool Fetch::_is_stopped() const {
    const std::scoped_lock lock(m_mutex);

    return m_stopped;
}

// Waits until until, or until stop() is called.
void Fetch::_wait_until(Clock::time_point until) {
    std::unique_lock lock(m_mutex);

    m_stopping.wait_for(lock, until - m_clock(), [this] { return m_stopped; });
}

// Holds off a new search for identifier for the failed search hold.
void Fetch::_hold_off(const Identifier& identifier) {
    const std::scoped_lock lock(m_mutex);

    m_held[identifier.to_string()] = m_clock() + m_settings.failed_search_hold;
}

// Lets a new search for identifier start, as this one has ended.
void Fetch::_end(const Identifier& identifier) {
    const std::scoped_lock lock(m_mutex);

    m_under_way.erase(identifier.to_string());
}

} // namespace peer
