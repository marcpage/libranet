#include "NodesHandler.hpp"

#include <map>
#include <mutex>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "logger/Logger.hpp"
#include "msg/Channel.hpp"
#include "protocol/NodeList.hpp"
#include "stats/NodeAddresses.hpp"
#include "stats/Recorder.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"
#include "web/ConnectionInfo.hpp"

using http::Problem;
using http::Request;
using http::Response;
using http::ResponseCode;
using msg::Channel;
using protocol::NodeList;
using stats::NodeAddresses;
using stats::Recorder;
using std::invalid_argument;
using std::map;
using std::nullopt;
using std::optional;
using std::scoped_lock;
using std::set;
using std::string;
using std::string_view;
using std::vector;
using storage::Identifier;
using types::Converter;
using types::json_content_type;
using types::nodes_path;
using web::ConnectionInfo;

namespace {

using Source = NodeAddresses::Source;
using Status = ResponseCode::Status;

// The methods /data/nodes takes, as Allow names them.
constexpr string_view allowed_methods = "GET, POST";

// list, as it is sent.
string body_of(const NodeList& list) {
    return Converter::text_of(list.body());
}

// The list the body of request, on connection, names, or nothing when it
// names none, having answered 400 saying why (HttpApi §10.5).
optional<NodeList> list_in(const Request& request, Response& response,
                           const ConnectionInfo& connection) {
    try {
        return NodeList::from_body(Converter::bytes_of(request.body()));
    } catch (const invalid_argument& error) {
        lDebug << "Refused the node list from" << connection.remote_address().to_string() + ":"
               << error.what();
        Problem(Problem::Type::InvalidList, Status::BadRequest, error.what()).answer(response);
        return nullopt;
    }
}

// The endpoints list names node_id at.
set<string> endpoints_of(const NodeList& list, const string& node_id) {
    set<string> endpoints;

    for (const auto& entry : list.entries()) {
        if (entry.node_id.to_string() == node_id) {
            endpoints.insert(entry.endpoint);
        }
    }

    return endpoints;
}

// How each of sender's own entries of received, the list sent once
// resolved, was learned, by endpoint: advertised where sent names it there
// as it is, and observed where it was resolved to the address the list came
// from. received's other entries, which sender relayed, are left out, as
// are all of them when there is no sender.
map<string, Source> sources_of(const NodeList& sent, const NodeList& received,
                               const optional<Identifier>& sender) {
    map<string, Source> sources;

    if (!sender) {
        return sources;
    }

    const auto sender_id = sender->to_string();
    const auto as_sent = endpoints_of(sent, sender_id);

    for (const auto& endpoint : endpoints_of(received, sender_id)) {
        sources[endpoint] = 0 == as_sent.count(endpoint) ? Source::Observed : Source::Advertised;
    }

    return sources;
}

} // namespace

namespace api {

NodesHandler::NodesHandler(const Identifier& own_id, const vector<string>& own_endpoints)
    : m_body(body_of(NodeList(own_id, own_endpoints))) {}

void NodesHandler::set_list(const NodeList& new_list) {
    auto body = body_of(new_list);
    const scoped_lock lock(m_mutex);

    m_body = std::move(body);
}

void NodesHandler::operator()(const Request& request, Response& response,
                              const ConnectionInfo& connection) const {
    const auto uri = request.uri();

    if (uri.substr(0, uri.find('?')) != nodes_path) {
        Problem(Status::NotFound, "No resource exists at this path.").answer(response);
        return;
    }

    const auto method = request.method();

    if ("GET" == method) {
        _get(response);
    } else if ("POST" == method) {
        _post(request, response, connection);
    } else {
        Problem::answer_method_not_allowed(request, allowed_methods, response);
    }
}

// The list the node serves, as it is sent.
void NodesHandler::_get(Response& response) const {
    string body;

    {
        const scoped_lock lock(m_mutex);

        body = m_body;
    }

    response.set_status_code(Status::Ok);
    response.headers().set("Content-Type", string(json_content_type));
    response.set_body(std::move(body));
}

// Tells the node's other parts of the list a peer sent, resolved by the
// address its connection came from; or says why it is not one.
void NodesHandler::_post(const Request& request, Response& response,
                         const ConnectionInfo& connection) {
    const auto sent = list_in(request, response, connection);

    if (!sent) {
        return;
    }

    auto received = sent->resolved(connection.remote_address().host());
    auto sources = sources_of(*sent, received, connection.node());

    Channel::post(Recorder::NodesReceived::name,
                  Recorder::NodesReceived{std::move(received), std::move(sources)});
    response.set_status_code(Status::Accepted);
}

} // namespace api
