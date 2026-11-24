#include "HandOff.hpp"

#include <algorithm>
#include <cstddef>
#include <exception>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "PeerSession.hpp"
#include "http/Request.hpp"
#include "logger/Logger.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Converter.hpp"

using http::Request;
using peer::HandOff;
using peer::PeerSession;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::size_t;
using std::string;
using std::string_view;
using std::vector;
using storage::HashPrefix;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;

namespace {

using Session = HandOff::Session;

// The type of content put (HttpApi §7).
constexpr string_view octet_stream = "application/octet-stream";

// How many leading bits session's node id shares with identifier's hash.
size_t matching_bits(const PeerSession& session, const Identifier& identifier) {
    return HashPrefix::parse(session.node_id().hash()).matching_bits(identifier);
}

// The sessions of peers still open, those whose node ids best match
// identifier first, and those that match alike in the order given.
vector<Session> ranked(vector<Session> peers, const Identifier& identifier) {
    const auto is_closed = [](const Session& session) { return !session || !session->is_open(); };

    peers.erase(std::remove_if(peers.begin(), peers.end(), is_closed), peers.end());
    std::stable_sort(peers.begin(), peers.end(),
                     [&identifier](const Session& left, const Session& right) {
        return matching_bits(*left, identifier) > matching_bits(*right, identifier);
    });
    return peers;
}

// A PUT of body, as identifier's content (HttpApi §7).
Request offer_of(const Identifier& identifier, string body) {
    Request request;

    request.set_method("PUT");
    request.set_uri("/data/" + identifier.to_string());
    request.headers().set("Content-Type", string(octet_stream));
    request.set_body(std::move(body));
    return request;
}

// Logs that identifier could not be offered to session's node, and why.
void report_unoffered(const PeerSession& session, const Identifier& identifier,
                      const std::exception& error) {
    lDebug << "Could not offer" << identifier.to_string() << "to"
           << session.address().to_string() << ":" << error.what();
}

// Whether session's node took identifier's content, offered as body. It did
// not when it could not be asked: its connection closed or failed, or its
// answer could not be read or shown to come from it, each of which ends the
// session.
bool took(PeerSession& session, const Identifier& identifier, const string& body) {
    try {
        const auto answers = session.exchange({offer_of(identifier, body)});
        const auto& code = answers.at(0).code();

        if (code.is_success()) {
            return true;
        }

        lDebug << session.address().to_string() << "did not take" << identifier.to_string()
               << ": it answered" << code.code();
    } catch (const std::runtime_error& error) {
        report_unoffered(session, identifier, error);
    } catch (const invalid_argument& error) {
        report_unoffered(session, identifier, error);
    }

    return false;
}

} // namespace

namespace peer {

HandOff::HandOff(IDataSource::Ptr store, GetPeers get_peers)
    : m_store(std::move(store))
    , m_get_peers(std::move(get_peers)) {
    if (!m_store) {
        throw invalid_argument("A hand-off needs the store that holds what it offers");
    }

    if (!m_get_peers) {
        throw invalid_argument("A hand-off needs the peers to offer it to");
    }
}

optional<Identifier> HandOff::hand_off(const Identifier& identifier) const {
    const auto stored = m_store->get(identifier);

    if (!stored) {
        lDebug << "Not handing off" << identifier.to_string() << ", which is not held";
        return nullopt;
    }

    const auto body = Converter::text_of(*stored);

    for (const auto& session : ranked(m_get_peers(), identifier)) {
        if (took(*session, identifier, body)) {
            return session->node_id();
        }
    }

    lDebug << "No peer took" << identifier.to_string();
    return nullopt;
}

} // namespace peer
