#include "NodeHandler.hpp"

#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "net/Address.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"
#include "types/Json.hpp"
#include "web/ConnectionInfo.hpp"

using config::NodeHandler;
using http::Problem;
using http::Request;
using http::Response;
using net::Address;
using std::invalid_argument;
using std::string;
using std::vector;
using storage::Identifier;
using types::Json;
using types::json_content_type;
using web::ConnectionInfo;

namespace {

using Status = http::ResponseCode::Status;

// The endpoint the node advertises: the first of own_endpoints. Throws
// invalid_argument when there is none.
const string& advertised(const vector<string>& own_endpoints) {
    if (own_endpoints.empty()) {
        throw invalid_argument("The node must be named at an endpoint to say where it is reached");
    }

    return own_endpoints.front();
}

// What a GET of /config/api/node is answered: the node's id, where its main
// port listens, and the endpoint it advertises, as the Python node names
// them.
string description_of(const Identifier& own_id, const Address& main_address,
                      const vector<string>& own_endpoints) {
    return Json(Json::Object{
                    {"node_id", Json(own_id.to_string())},
                    {"listen_address", Json(main_address.host())},
                    {"listen_port", Json(int{main_address.port()})},
                    {"advertised_endpoint", Json(advertised(own_endpoints))},
                })
        .dump();
}

} // namespace

namespace config {

NodeHandler::NodeHandler(const Identifier& own_id, const Address& main_address,
                         const vector<string>& own_endpoints)
    : m_body(description_of(own_id, main_address, own_endpoints)) {}

void NodeHandler::operator()(const Request& request, Response& response,
                             [[maybe_unused]] const ConnectionInfo& connection) const {
    const auto uri = request.uri();

    if (uri.substr(0, uri.find('?')) != path) {
        Problem(Status::NotFound, "No resource exists at this path.").answer(response);
        return;
    }

    if ("GET" != request.method()) {
        Problem::answer_method_not_allowed(request, "GET", response);
        return;
    }

    response.set_status_code(Status::Ok);
    response.headers().set("Content-Type", string(json_content_type));
    response.set_body(m_body);
}

} // namespace config
