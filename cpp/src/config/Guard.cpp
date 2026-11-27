#include "Guard.hpp"

#include <string>
#include <utility>

#include "Credential.hpp"
#include "http/BasicCredentials.hpp"
#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "logger/Logger.hpp"
#include "net/IpAddress.hpp"
#include "web/ConnectionInfo.hpp"

using config::Guard;
using http::BasicCredentials;
using http::Problem;
using http::Request;
using http::Response;
using net::IpAddress;
using std::string;
using web::ConnectionInfo;

namespace {

using Status = http::ResponseCode::Status;

// Whether the client that sent a request on connection is on this machine: it
// came from a loopback address, or from the address of this machine it
// reached, which a client elsewhere cannot come from, since the replies to it
// never leave this machine (HTTP API §2.3). An IPv4-mapped address is the
// IPv4 address it maps, and one that is no IP address is no client's here.
bool is_local(const ConnectionInfo& connection) {
    const auto source = IpAddress::parse(connection.remote_address().host());

    if (!source) {
        return false;
    }

    if (source->is_loopback()) {
        return true;
    }

    const auto reached = IpAddress::parse(connection.local_address().host());

    return reached && reached->to_string() == source->to_string();
}

// Answers 403 for a request from a client elsewhere (HTTP API §2.3).
void refuse_elsewhere(Response& response) {
    Problem(Status::Forbidden, "/config is served only to clients on this machine.")
        .answer(response);
}

// Answers 401, with the challenge that has a browser ask for a username and
// password (RFC 7617 §2, §2.1).
void challenge(Response& response) {
    Problem(Problem::Type::CredentialRequired, Status::Unauthorized,
            "/config requires the username and password this node captured.")
        .answer(response);
    response.headers().set("WWW-Authenticate",
                           R"(Basic realm=")" + string(Guard::realm) + R"(", charset="UTF-8")");
}

} // namespace

namespace config {

Guard::Guard(Credential credential)
    : m_credential(std::move(credential)) {}

Guard::Handler Guard::in_front_of(Handler handler) const {
    return [guard = *this, handler = std::move(handler)](const Request& request,
                                                         Response& response,
                                                         ConnectionInfo& connection) {
        guard._serve(handler, request, response, connection);
    };
}

// Has handler answer request, if it is from a client on this machine and
// gives the credential.
void Guard::_serve(const Handler& handler, const Request& request, Response& response,
                   ConnectionInfo& connection) const {
    if (!is_local(connection)) {
        lDebug << "Refused" << request.method() << request.uri() << "from"
               << connection.remote_address().to_string() + ":"
               << "not a client on this machine";
        refuse_elsewhere(response);
        return;
    }

    if (!_authenticates(request)) {
        challenge(response);
        return;
    }

    handler(request, response, connection);
}

// Whether request gives the credential, capturing it when there is none yet.
// A field that cannot be read gives none, so that it never captures one.
bool Guard::_authenticates(const Request& request) const {
    const auto field = request.headers().get(BasicCredentials::field_name);

    if (!field) {
        return false;
    }

    const auto credentials = BasicCredentials::parse(*field);

    return credentials && m_credential.authenticate(credentials->user_pass());
}

} // namespace config
