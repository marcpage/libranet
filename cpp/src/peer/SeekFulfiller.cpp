#include "SeekFulfiller.hpp"

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <exception>
#include <future>
#include <iterator>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "PeerSession.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "logger/Logger.hpp"
#include "protocol/SearchResults.hpp"
#include "protocol/SeekList.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "storage/SearchAnswers.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"

using http::Request;
using http::Response;
using peer::PeerSession;
using protocol::SearchResults;
using protocol::SeekList;
using std::exception;
using std::future;
using std::invalid_argument;
using std::min;
using std::nullopt;
using std::optional;
using std::ptrdiff_t;
using std::scoped_lock;
using std::set;
using std::size_t;
using std::string;
using std::to_string;
using std::vector;
using storage::HashPrefix;
using storage::IDataSource;
using storage::Identifier;
using storage::SearchAnswers;
using types::Converter;
using types::data_path_prefix;
using types::json_content_type;
using types::octet_stream_content_type;
using types::search_path_prefix;
using types::seek_path;
using Status = http::ResponseCode::Status;

namespace {

using Matches = std::map<string, vector<Identifier>>; // By prefix

// store, once it is known to be one. Throws invalid_argument for none.
IDataSource::Ptr checked(IDataSource::Ptr store) {
    if (!store) {
        throw invalid_argument("A seek fulfiller needs a store to put and name what it holds from");
    }

    return store;
}

// settings, once each is known to be one that can be worked with. Throws
// invalid_argument, naming the setting, for one that cannot.
peer::SeekFulfiller::Settings checked(const peer::SeekFulfiller::Settings& settings) {
    if (settings.refresh <= peer::SeekFulfiller::Milliseconds::zero()) {
        throw invalid_argument("peers.seek_refresh_seconds must be more than zero: "
                               + to_string(settings.refresh.count()) + " ms");
    }

    if (0 == settings.pipeline_depth) {
        throw invalid_argument("peers.pipeline_depth must be at least 1");
    }

    return settings;
}

// clock, once it is known to be a function. Throws invalid_argument for none.
peer::SeekFulfiller::ClockFunction checked(peer::SeekFulfiller::ClockFunction clock) {
    if (!clock) {
        throw invalid_argument("A seek fulfiller needs the time");
    }

    return clock;
}

// What store holds whose hash begins with a prefix, for a search of it.
SearchAnswers::Held held_in(const IDataSource::Ptr& store) {
    return [store](const HashPrefix& prefix) { return store->starting_with(prefix); };
}

// items, in runs of no more than depth, in order.
template<typename Item>
vector<vector<Item>> runs_of(const vector<Item>& items, size_t depth) {
    vector<vector<Item>> runs;

    for (size_t start = 0; start < items.size(); start += depth) {
        const auto first = std::next(items.begin(), static_cast<ptrdiff_t>(start));
        const auto count = min(depth, items.size() - start);

        runs.emplace_back(first, std::next(first, static_cast<ptrdiff_t>(count)));
    }

    return runs;
}

// The node of session, and where it is, as a message names it.
string described(const PeerSession& session) {
    return session.node_id().to_string() + " at " + session.address().to_string();
}

// The request for a node's seek list.
Request seek_get() {
    Request request;

    request.set_method("GET");
    request.set_uri(string(seek_path));
    return request;
}

// The put of body, the content identifier names as it is stored (HttpApi
// §7), which may be compressed.
Request put_of(const Identifier& identifier, const IDataSource::Data& body) {
    Request request;

    request.set_method("PUT");
    request.set_uri(string(data_path_prefix) + identifier.to_string());
    request.headers().set("Content-Type", string(octet_stream_content_type));
    request.set_body(Converter::text_of(body));
    return request;
}

// The post naming identifiers, best match first, for the search entry
// prefix, as a search's answer is sent (HttpApi §10.7.2): compressed only
// where it must be to stay within 1 MiB, and then said to be.
Request post_of(const string& prefix, const vector<Identifier>& identifiers) {
    auto body = SearchResults(identifiers).body();
    Request request;

    request.set_method("POST");
    request.set_uri(string(search_path_prefix) + prefix);
    request.headers().set("Content-Type", string(json_content_type));

    if (body.compressed) {
        request.headers().set("Content-Encoding", "deflate");
    }

    request.set_body(std::move(body.text));
    return request;
}

// The seek list answer carries, from the node from describes, decompressed
// to no more than list_bytes: nothing when it carries none that can be read,
// as when it is not 200 OK.
optional<SeekList> seek_list_in(const Response& answer, const string& from, size_t list_bytes) {
    if (answer.code() != Status::Ok) {
        lDebug << from << "sent no seek list but answered" << answer.status_code();
        return nullopt;
    }

    try {
        return SeekList::from_body(Converter::bytes_of(answer.body()), list_bytes);
    } catch (const invalid_argument& error) {
        lDebug << "Ignoring the seek list from" << from + ":" << error.what();
        return nullopt;
    }
}

// Whether answer says a put was taken: the content was stored, or was held.
bool is_taken(const Response& answer) {
    return answer.code().is_success();
}

// What a search of each of searches finds in found, best first, less what
// pushed names, by prefix, each once.
Matches matches_of(SearchAnswers& found, const vector<HashPrefix>& searches,
                   const set<string>& pushed) {
    Matches matches;

    for (const auto& prefix : searches) {
        auto [matching, is_new] = matches.try_emplace(prefix.to_string());

        if (!is_new) {
            continue;
        }

        for (auto& identifier : found.answer(prefix)) {
            if (pushed.end() == pushed.find(identifier.to_string())) {
                matching->second.push_back(std::move(identifier));
            }
        }
    }

    return matches;
}

} // namespace

namespace peer {

SeekFulfiller::SeekFulfiller(IDataSource::Ptr store, const Settings& settings, ClockFunction clock)
    : m_store(checked(std::move(store)))
    , m_settings(checked(settings))
    , m_clock(checked(std::move(clock)))
    , m_found(held_in(m_store), m_settings.search_min_bits, m_settings.refresh, m_clock) {}

void SeekFulfiller::fulfill(PeerSession& session, const SeekList& seek) {
    const auto now = m_clock();

    {
        const scoped_lock lock(m_mutex);

        m_served.insert_or_assign(session.node_id().to_string(), Served{{}, {}, now});
    }

    _fulfill(session, seek);
}

size_t SeekFulfiller::refresh(const Sessions& sessions) {
    const auto due = _due(sessions);
    vector<future<bool>> reads;
    size_t read = 0;

    reads.reserve(due.size());
    std::transform(due.begin(), due.end(), std::back_inserter(reads),
                   [this](const std::shared_ptr<PeerSession>& session) {
        return std::async(std::launch::async, [this, session] { return _refresh(*session); });
    });

    for (auto& reading : reads) {
        if (reading.get()) {
            ++read;
        }
    }

    return read;
}

size_t SeekFulfiller::pipeline_depth() const noexcept {
    return m_settings.pipeline_depth;
}

size_t SeekFulfiller::list_bytes() const noexcept {
    return m_settings.list_bytes;
}

// Of sessions, those that are open and whose node's list is due to be read,
// the first of each node's, each now marked as read now. What was pushed and
// named to a node none is open to is let go once its list is due.
SeekFulfiller::Sessions SeekFulfiller::_due(const Sessions& sessions) {
    ByNode open;

    for (const auto& session : sessions) {
        if (session && session->is_open()) {
            open.emplace(session->node_id().to_string(), session);
        }
    }

    const auto now = m_clock();
    const scoped_lock lock(m_mutex);
    Sessions due;

    _let_go(open, now);

    for (const auto& [node_id, session] : open) {
        auto [served, is_new] = m_served.try_emplace(node_id);

        if (is_new || now - served->second.read_at >= m_settings.refresh) {
            served->second.read_at = now;
            due.push_back(session);
        }
    }

    return due;
}

// Lets go of what was pushed and named to each node that is not among open,
// and whose list is due at now. The caller holds m_mutex.
void SeekFulfiller::_let_go(const ByNode& open, Clock::time_point now) {
    for (auto served = m_served.begin(); served != m_served.end();) {
        if (open.end() == open.find(served->first)
            && now - served->second.read_at >= m_settings.refresh) {
            served = m_served.erase(served);
        } else {
            ++served;
        }
    }
}

// Reads the seek list of the node of session again and fulfills it, and says
// whether it was read. What fails is logged.
bool SeekFulfiller::_refresh(PeerSession& session) {
    const auto from = described(session);

    try {
        const auto answers = session.exchange({seek_get()});
        const auto seek = seek_list_in(answers.at(0), from, m_settings.list_bytes);

        if (seek) {
            _fulfill(session, *seek);
        }

        return seek.has_value();
    } catch (const exception& error) {
        lInfo << "Could not fulfill the seek list of" << from + ":" << error.what();
        return false;
    }
}

// Puts what seek names that the store holds, then names what matches the
// searches it names.
void SeekFulfiller::_fulfill(PeerSession& session, const SeekList& seek) {
    _push(session, seek.data());
    _name(session, seek.search());
}

// Puts to the node of session each of sought that the store holds and that
// has not been pushed to it, once, in runs of peers.pipeline_depth.
void SeekFulfiller::_push(PeerSession& session, const vector<Identifier>& sought) {
    const auto pushed = _pushed(session.node_id().to_string());
    set<string> chosen;
    vector<Identifier> wanted;

    for (const auto& identifier : sought) {
        const auto name = identifier.to_string();

        if (pushed.end() == pushed.find(name) && m_store->has(identifier)
            && chosen.insert(name).second) {
            wanted.push_back(identifier);
        }
    }

    for (const auto& run : runs_of(wanted, m_settings.pipeline_depth)) {
        _push_run(session, run);
    }
}

// Puts to the node of session each of run the store still holds, and notes
// each as pushed to it once it is answered. One not taken is logged.
void SeekFulfiller::_push_run(PeerSession& session, const vector<Identifier>& run) {
    vector<Identifier> sent;
    vector<Request> puts;

    for (const auto& identifier : run) {
        if (const auto body = m_store->get(identifier)) {
            puts.push_back(put_of(identifier, *body));
            sent.push_back(identifier);
        }
    }

    if (puts.empty()) {
        return;
    }

    const auto answers = session.exchange(std::move(puts));

    _note_pushed(session.node_id().to_string(), sent);

    for (size_t index = 0; index < answers.size(); ++index) {
        if (!is_taken(answers.at(index))) {
            lDebug << described(session) << "did not take" << sent.at(index).to_string()
                   << "but answered" << answers.at(index).status_code();
        }
    }
}

// Posts to the node of session, for each of searches, what the store holds
// that a search of it finds, less what has been pushed to the node, or named
// to it while the entry has been listed, in runs of peers.pipeline_depth.
// What is posted counts as named from now on. One not accepted is logged.
void SeekFulfiller::_name(PeerSession& session, const vector<HashPrefix>& searches) {
    const auto node_id = session.node_id().to_string();
    const auto unnamed = _unnamed(node_id, matches_of(m_found, searches, _pushed(node_id)));
    vector<string> prefixes;

    for (const auto& [prefix, identifiers] : unnamed) {
        if (!identifiers.empty()) {
            prefixes.push_back(prefix);
        }
    }

    for (const auto& run : runs_of(prefixes, m_settings.pipeline_depth)) {
        vector<Request> posts;

        posts.reserve(run.size());
        std::transform(run.begin(), run.end(), std::back_inserter(posts),
                       [&unnamed](const string& prefix) {
            return post_of(prefix, unnamed.at(prefix));
        });

        const auto answers = session.exchange(std::move(posts));

        for (size_t index = 0; index < answers.size(); ++index) {
            if (answers.at(index).code() != Status::Accepted) {
                lDebug << described(session) << "took no identifiers for" << run.at(index)
                       << "but answered" << answers.at(index).status_code();
            }
        }
    }
}

// What has been pushed to node_id on the connection to it now.
set<string> SeekFulfiller::_pushed(const string& node_id) {
    const scoped_lock lock(m_mutex);
    const auto served = m_served.find(node_id);

    if (m_served.end() == served) {
        return {};
    }

    return served->second.pushed;
}

// Notes that identifiers were pushed to node_id, unless what was pushed to it
// has been let go, as its connection has ended.
void SeekFulfiller::_note_pushed(const string& node_id, const vector<Identifier>& identifiers) {
    const scoped_lock lock(m_mutex);
    const auto served = m_served.find(node_id);

    if (m_served.end() == served) {
        return;
    }

    for (const auto& identifier : identifiers) {
        served->second.pushed.insert(identifier.to_string());
    }
}

// Of matches, by prefix, what has not been named to node_id while the prefix
// has been listed, which counts as named from now on. What was named for a
// prefix matches leaves out is let go, as the entry is no longer listed, so
// that what matches it is named again if it is listed again.
SeekFulfiller::Matches SeekFulfiller::_unnamed(const string& node_id, const Matches& matches) {
    const scoped_lock lock(m_mutex);
    const auto served = m_served.find(node_id);
    std::map<string, set<string>> named;
    Matches unnamed;

    if (m_served.end() == served) {
        return matches;
    }

    for (const auto& [prefix, matching] : matches) {
        const auto before = served->second.named.find(prefix);
        auto& listed = named[prefix];
        auto& fresh = unnamed[prefix];

        if (served->second.named.end() != before) {
            listed = before->second;
        }

        std::copy_if(matching.begin(), matching.end(), std::back_inserter(fresh),
                     [&listed](const Identifier& identifier) {
            return listed.insert(identifier.to_string()).second;
        });
    }

    served->second.named = std::move(named);
    return unnamed;
}

} // namespace peer
