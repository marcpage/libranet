#include "Pusher.hpp"

#include <algorithm>
#include <cstddef>
#include <exception>
#include <future>
#include <iterator>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "PeerSession.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "logger/Logger.hpp"
#include "msg/Channel.hpp"
#include "msg/IMessage.hpp"
#include "msg/Message.hpp"
#include "storage/DataStored.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"

using http::Request;
using http::Response;
using msg::Channel;
using msg::IMessage;
using msg::value_of;
using peer::PeerSession;
using peer::Pusher;
using std::exception;
using std::future;
using std::invalid_argument;
using std::map;
using std::nullopt;
using std::optional;
using std::size_t;
using std::string;
using std::to_string;
using std::vector;
using storage::DataStored;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;
using types::data_path_prefix;
using types::octet_stream_content_type;

// The thread takes data.stored, which others post from other files, so
// nothing here would make its message's virtual to_string(), which
// value_of() casts to.
template class msg::Message<DataStored>;

namespace {

using Session = Pusher::Session;

// What is bound for one peer: its session, and the content to put to it, in
// the order it was stored.
struct Bound {
    Session session;
    vector<Identifier> identifiers;
};

// store, once it is known to be one. Throws invalid_argument when it is none.
IDataSource::Ptr checked_store(IDataSource::Ptr store) {
    if (!store) {
        throw invalid_argument("A pusher needs a store to read what it pushes");
    }

    return store;
}

// get_peers, once it is known to be a function. Throws invalid_argument when
// it is not.
Pusher::GetPeers checked_get_peers(Pusher::GetPeers get_peers) {
    if (!get_peers) {
        throw invalid_argument("A pusher needs the peers to push to");
    }

    return get_peers;
}

// run_length, once it is known to be more than zero. Throws invalid_argument
// when it is not.
size_t checked_run_length(size_t run_length) {
    if (0 == run_length) {
        throw invalid_argument("peers.pipeline_depth is not more than zero");
    }

    return run_length;
}

// Reports what the thread could not handle.
void log_error(const string& text) {
    lErr << text;
}

// The best outgoing connection for identifier: the open one of sessions
// whose node id shares the most leading bits with its hash, and the first
// given of those that share alike. Nothing when none is open.
Session best_for(const vector<Session>& sessions, const Identifier& identifier) {
    Session best;
    size_t best_bits = 0;

    for (const auto& session : sessions) {
        if (!session || !session->is_open()) {
            continue;
        }

        const auto bits = session->matching_bits(identifier);

        if (!best || bits > best_bits) {
            best = session;
            best_bits = bits;
        }
    }

    return best;
}

// What of stored goes to each peer of sessions, by its node id: each to its
// best outgoing connection, unless that is the node it came from.
map<string, Bound> bound_for(const Pusher::Stored& stored, const vector<Session>& sessions) {
    map<string, Bound> bound;

    for (const auto& new_content : stored) {
        const auto& identifier = new_content.identifier;
        const auto session = best_for(sessions, identifier);

        if (!session) {
            lDebug << "No peer to push" << identifier.to_string() << "to";
            continue;
        }

        const auto node = session->node_id().to_string();

        if (new_content.node && new_content.node->to_string() == node) {
            lDebug << "Not pushing" << identifier.to_string() << "back to" << node;
            continue;
        }

        auto& to_peer = bound[node];

        to_peer.session = session;
        to_peer.identifiers.push_back(identifier);
    }

    return bound;
}

// identifiers, in the order given, in runs of run_length at most.
vector<vector<Identifier>> runs_of(const vector<Identifier>& identifiers, size_t run_length) {
    vector<vector<Identifier>> runs;

    for (const auto& identifier : identifiers) {
        if (runs.empty() || runs.back().size() >= run_length) {
            runs.emplace_back();
        }

        runs.back().push_back(identifier);
    }

    return runs;
}

// The put of identifier's content as store holds it (HttpApi §7); nothing
// once it is no longer held, as when it was let go of since it was stored.
optional<Request> put_of(const IDataSource& store, const Identifier& identifier) {
    const auto body = store.get(identifier);

    if (!body) {
        lDebug << identifier.to_string() << "is no longer held, so is not pushed";
        return nullopt;
    }

    Request request;

    request.set_method("PUT");
    request.set_uri(string(data_path_prefix) + identifier.to_string());
    request.headers().set("Content-Type", string(octet_stream_content_type));
    request.set_body(Converter::text_of(*body));
    return request;
}

// Whether answer, from session's node, took identifier's content: 201
// Created for content it did not hold, or 200 OK for content it did
// (HttpApi §7.1). One that did not is logged.
bool was_taken(const PeerSession& session, const Identifier& identifier,
               const Response& answer) {
    if (answer.code().is_success()) {
        lDebug << "Pushed" << identifier.to_string() << "to" << session.node_id().to_string();
        return true;
    }

    lInfo << session.node_id().to_string() << "at" << session.address().to_string()
          << "answered the push of" << identifier.to_string() << "with" << answer.status_code();
    return false;
}

// Puts what store still holds of run to session's node in one run of
// requests, and gives how many it took. Throws what PeerSession::exchange()
// throws.
size_t put_run(PeerSession& session, const IDataSource& store, const vector<Identifier>& run) {
    vector<Request> requests;
    vector<Identifier> put;
    size_t taken = 0;

    for (const auto& identifier : run) {
        if (auto request = put_of(store, identifier)) {
            requests.push_back(std::move(*request));
            put.push_back(identifier);
        }
    }

    if (requests.empty()) {
        return 0;
    }

    const auto answers = session.exchange(std::move(requests));

    for (size_t index = 0; index < put.size(); ++index) {
        if (was_taken(session, put.at(index), answers.at(index))) {
            ++taken;
        }
    }

    return taken;
}

// Puts what is bound for its peer in runs of run_length at most, one after
// another, and gives how many it took. A run that fails ends the pushes, as
// the session has ended, and is logged.
size_t put_all(const Bound& bound, const IDataSource& store, size_t run_length) {
    auto& session = *bound.session;
    size_t taken = 0;

    for (const auto& run : runs_of(bound.identifiers, run_length)) {
        try {
            taken += put_run(session, store, run);
        } catch (const exception& error) {
            lInfo << "Could not push" << to_string(run.size()) << "to"
                  << session.node_id().to_string() << "at"
                  << session.address().to_string() + ":" << error.what();
            break;
        }
    }

    return taken;
}

} // namespace

namespace peer {

Pusher::Pusher(IDataSource::Ptr store, GetPeers get_peers, size_t run_length)
    : m_store(checked_store(std::move(store)))
    , m_get_peers(checked_get_peers(std::move(get_peers)))
    , m_run_length(checked_run_length(run_length))
    , m_channel(DataStored::name, [this](const IMessage::Ptr& message) { _handle(message); },
                log_error) {}

size_t Pusher::push(const Stored& stored) const {
    const auto bound = bound_for(stored, m_get_peers());
    vector<future<size_t>> pushes;
    size_t taken = 0;

    std::transform(bound.begin(), bound.end(), std::back_inserter(pushes),
                   [this](const auto& entry) {
        return std::async(std::launch::async, [this, to_peer = entry.second] {
            return put_all(to_peer, *m_store, m_run_length);
        });
    });

    for (auto& pushed : pushes) {
        taken += pushed.get();
    }

    return taken;
}

void Pusher::_handle(const IMessage::Ptr& message) {
    const auto stored = _take_waiting(message);

    if (!stored.empty()) {
        (void)push(stored);
    }
}

// The new content first announces, and what else is waiting to be pushed
// behind it, a run's length in all at most. Nothing is waited for, so
// content is taken together only when it was stored while the last was
// pushed.
Pusher::Stored Pusher::_take_waiting(const IMessage::Ptr& first) {
    Stored stored;
    optional<IMessage::Ptr> next = first;

    while (next) {
        if (const auto* const new_content = value_of<DataStored>(*next)) {
            stored.push_back(*new_content);
        } else if (*next) {
            lWarn << "Not new content to push:" << (*next)->to_string();
        }

        if (stored.size() >= m_run_length) {
            break;
        }

        next = m_channel.next_message(Channel::Milliseconds::zero());
    }

    return stored;
}

} // namespace peer
