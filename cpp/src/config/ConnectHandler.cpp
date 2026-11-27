#include "ConnectHandler.hpp"

#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

#include "DialOutcomes.hpp"
#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "logger/Logger.hpp"
#include "msg/Channel.hpp"
#include "peer/Connections.hpp"
#include "protocol/DialRequest.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"
#include "types/Json.hpp"
#include "web/ConnectionInfo.hpp"

using config::ConnectHandler;
using config::DialOutcomes;
using http::Problem;
using http::Request;
using http::Response;
using msg::Channel;
using protocol::DialRequest;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::string;
using std::string_view;
using types::Converter;
using types::Json;
using types::json_content_type;
using web::ConnectionInfo;
using AddressDialed = DialOutcomes::AddressDialed;
using DialRequested = peer::Connections::DialRequested;
using Outcome = AddressDialed::Outcome;

namespace {

using Status = http::ResponseCode::Status;

// The one method the path takes, as Allow names it.
constexpr string_view allowed_methods = "POST";

// Why no node was reached, when the dial does not say.
constexpr string_view no_reason = "no reason was given";

// Whether uri's path, less any query, is the handler's own, and not one
// beneath it.
bool is_connections_path(string_view uri) {
    return ConnectHandler::path == uri.substr(0, uri.find('?'));
}

// Whether request says its body is JSON: its Content-Type is
// application/json, in any case, whatever parameters follow it, such as a
// charset.
bool carries_json(const Request& request) {
    const auto field = request.headers().get("Content-Type", "");
    const auto media_type = Converter::trimmed(field.substr(0, field.find(';')));

    return Converter::lower_case(media_type) == json_content_type;
}

// Answers 415 for request, whose body does not say it is JSON.
void refuse_media_type(const Request& request, Response& response) {
    const auto named = Converter::trimmed(request.headers().get("Content-Type", ""));
    const auto instead = named.empty() ? string("and this request names none")
                                       : "not " + string(named);

    Problem(Status::UnsupportedMediaType, "A request body's Content-Type must be "
                                              + string(json_content_type) + ", " + instead)
        .answer(response);
}

// The address request, on connection, asks to have dialed; or nothing, having
// answered 415 for a body that does not say it is JSON, or 400 for one that
// names no address the node dials, saying why.
optional<DialRequest> asked_for(const Request& request, Response& response,
                                const ConnectionInfo& connection) {
    if (!carries_json(request)) {
        lDebug << "Refused" << request.method() << request.uri() << "from"
               << connection.remote_address().to_string() + ":"
               << "its body is not said to be JSON";
        refuse_media_type(request, response);
        return nullopt;
    }

    try {
        return DialRequest::parse(request.body());
    } catch (const invalid_argument& error) {
        lDebug << "Refused" << request.method() << request.uri() << "from"
               << connection.remote_address().to_string() + ":" << error.what();
        Problem(Problem::Type::InvalidRequest, Status::BadRequest, error.what()).answer(response);
        return nullopt;
    }
}

// Answers 200 for the node dialed reached, named node_id, at endpoint, saying
// whether it was connected already.
void answer_reached(const string& endpoint, const string& node_id, const AddressDialed& dialed,
                    Response& response) {
    const Json answer(Json::Object{
        {string(DialRequest::address_key), Json(endpoint)},
        {"node_id", Json(node_id)},
        {"outcome", Json(dialed.outcome_name())},
    });

    response.set_status_code(Status::Ok);
    response.headers().set("Content-Type", string(json_content_type));
    response.set_body(answer.dump());
}

// Answers what came of dialing endpoint, as dialed says, or 504 when nothing
// came in time.
void answer(const string& endpoint, const optional<AddressDialed>& dialed, Response& response) {
    if (!dialed) {
        lInfo << "Nothing came of dialing" << endpoint << "in time";
        Problem(Status::GatewayTimeout,
                "The node did not say in time whether it reached " + endpoint + ". It may yet.")
            .answer(response);
        return;
    }

    const auto reached =
        Outcome::Connected == dialed->outcome || Outcome::AlreadyConnected == dialed->outcome;

    if (reached && dialed->node_id) {
        answer_reached(endpoint, dialed->node_id->to_string(), *dialed, response);
    } else if (Outcome::ThisNode == dialed->outcome) {
        Problem(Status::Conflict, endpoint + " is this node's own address.").answer(response);
    } else if (Outcome::Unreached == dialed->outcome) {
        const auto why = dialed->detail.empty() ? string(no_reason) : dialed->detail;

        Problem(Status::BadGateway, "No node was reached at " + endpoint + ": " + why)
            .answer(response);
    } else {
        lErr << "Dialing" << endpoint << "came to" << string(*dialed) + ","
             << "which names no node reached";
        Problem(Status::InternalServerError, "What came of dialing " + endpoint + " is unknown.")
            .answer(response);
    }
}

} // namespace

namespace config {

ConnectHandler::ConnectHandler(DialOutcomes& outcomes)
    : m_outcomes(&outcomes) {}

void ConnectHandler::operator()(const Request& request, Response& response,
                                const ConnectionInfo& connection) const {
    if (!is_connections_path(request.uri())) {
        Problem(Status::NotFound, "No resource exists at this path.").answer(response);
        return;
    }

    if ("POST" != request.method()) {
        Problem::answer_method_not_allowed(request, allowed_methods, response);
        return;
    }

    const auto asked = asked_for(request, response, connection);

    if (!asked) {
        return;
    }

    const auto& endpoint = asked->endpoint();
    const auto dialed = m_outcomes->await([&endpoint](const string& dial_id) {
        Channel::post(DialRequested::name, DialRequested{dial_id, endpoint});
    });

    answer(endpoint, dialed, response);
}

} // namespace config
