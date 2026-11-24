#include "NodeListPublisher.hpp"

#include <cstddef>
#include <exception>
#include <future>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "PeerSession.hpp"
#include "http/Request.hpp"
#include "http/ResponseCode.hpp"
#include "logger/Logger.hpp"
#include "protocol/NodeList.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"

using http::Request;
using http::ResponseCode;
using peer::NodeListPublisher;
using peer::PeerSession;
using protocol::NodeList;
using std::exception;
using std::future;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::size_t;
using std::string;
using std::to_string;
using std::vector;
using storage::Identifier;
using types::Converter;
using types::json_content_type;
using types::nodes_path;

namespace {

// own_id, once it is known to have a hash. Throws invalid_argument when it
// has none.
Identifier checked_id(const Identifier& own_id) {
    if (own_id.hash().empty()) {
        throw invalid_argument("A node list publisher needs the id of this node");
    }

    return own_id;
}

// The post of list, as it is sent: JSON, compressed only when it must be to
// stay within the size a list may be (HttpApi §10.6), as the Python node
// sends it, under the type of JSON either way.
Request post_of(const NodeList& list) {
    Request request;

    request.set_method("POST");
    request.set_uri(string(nodes_path));
    request.headers().set("Content-Type", string(json_content_type));
    request.set_body(Converter::text_of(list.body()));
    return request;
}

// Posts list to the node of session, and says whether it accepted it. What
// fails is logged.
bool post(PeerSession& session, const NodeList& list) {
    const auto told = to_string(list.entries().size()) + " entries to "
                      + session.node_id().to_string() + " at " + session.address().to_string();

    try {
        const auto answers = session.exchange({post_of(list)});
        const auto status = answers.at(0).status_code();

        if (static_cast<int>(ResponseCode::Status::Accepted) == status) {
            lDebug << "Posted a node list of" << told;
            return true;
        }

        lWarn << "A node list of" << told << "was answered" << status;
    } catch (const exception& error) {
        lInfo << "Could not post a node list of" << told << ":" << error.what();
    }

    return false;
}

} // namespace

namespace peer {

NodeListPublisher::NodeListPublisher(const Identifier& own_id,
                                     const vector<string>& own_endpoints)
    : m_own_id(checked_id(own_id))
    , m_own_entries(m_own_id, own_endpoints) {}

optional<NodeList> NodeListPublisher::list_for(const NodeList& added,
                                               const Identifier& node_id) const {
    const auto peer_id = node_id.to_string();
    const auto own_id = m_own_id.to_string();
    auto list = m_own_entries;

    for (const auto& entry : added.entries()) {
        const auto named = entry.node_id.to_string();

        if (named != peer_id && named != own_id) {
            list.add(entry.endpoint, entry.node_id);
        }
    }

    // An entry at one of this node's own endpoints adds nothing.
    if (list.entries().size() == m_own_entries.entries().size()) {
        return nullopt;
    }

    return list;
}

size_t NodeListPublisher::publish(const NodeList& added, const Sessions& sessions) const {
    vector<future<bool>> posts;
    size_t accepted = 0;

    for (const auto& session : sessions) {
        if (!session || !session->is_open()) {
            continue;
        }

        auto list = list_for(added, session->node_id());

        if (list) {
            posts.push_back(std::async(std::launch::async, [session, to_post = std::move(*list)] {
                return post(*session, to_post);
            }));
        }
    }

    for (auto& posted : posts) {
        if (posted.get()) {
            ++accepted;
        }
    }

    return accepted;
}

} // namespace peer
