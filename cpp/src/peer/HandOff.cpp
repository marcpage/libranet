#include "HandOff.hpp"

#include <algorithm>
#include <exception>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "PeerSession.hpp"
#include "http/Request.hpp"
#include "logger/Logger.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"

using http::Request;
using peer::PeerSession;
using std::exception;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::runtime_error;
using std::string;
using std::string_view;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;
using types::data_path_prefix;
using types::octet_stream_content_type;

namespace {

// A PUT of body, as identifier's content (HttpApi §7).
Request offer_of(const Identifier& identifier, string body) {
    Request request;

    request.set_method("PUT");
    request.set_uri(string(data_path_prefix) + identifier.to_string());
    request.headers().set("Content-Type", string(octet_stream_content_type));
    request.set_body(std::move(body));
    return request;
}

// Logs that identifier could not be offered to session's node, and why.
void report_unoffered(const PeerSession& session, const Identifier& identifier,
                      const exception& error) {
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
    } catch (const runtime_error& error) {
        report_unoffered(session, identifier, error);
    } catch (const invalid_argument& error) {
        report_unoffered(session, identifier, error);
    }

    return false;
}

} // namespace

namespace peer {

HandOff::HandOff(IDataSource::Ptr store, Peers peers)
    : m_store(std::move(store))
    , m_peers(std::move(peers)) {
    if (!m_store) {
        throw invalid_argument("A hand-off needs the store that holds what it offers");
    }

    if (!m_peers) {
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
    const auto ranked = PeerSession::ranked(m_peers(), identifier);
    const auto taker = std::find_if(ranked.begin(), ranked.end(),
                                    [&identifier, &body](const Session& session) {
        return took(*session, identifier, body);
    });

    if (taker != ranked.end()) {
        return (*taker)->node_id();
    }

    lDebug << "No peer took" << identifier.to_string();
    return nullopt;
}

} // namespace peer
