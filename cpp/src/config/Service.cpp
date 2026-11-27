#include "Service.hpp"

#include <array>
#include <cstdint>
#include <exception>
#include <filesystem>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

#include "Credential.hpp"
#include "Guard.hpp"
#include "NotOffered.hpp"
#include "Page.hpp"
#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "identity/MessageSigner.hpp"
#include "identity/NodeIdentity.hpp"
#include "logger/Logger.hpp"
#include "net/Address.hpp"
#include "types/Constants.hpp"
#include "types/Json.hpp"
#include "web/ConnectionInfo.hpp"
#include "web/Server.hpp"

using config::Credential;
using config::Guard;
using config::NotOffered;
using config::Page;
using config::Service;
using http::Problem;
using http::Request;
using http::Response;
using identity::MessageSigner;
using identity::NodeIdentity;
using net::Address;
using std::errc;
using std::error_code;
using std::exception;
using std::invalid_argument;
using std::optional;
using std::string;
using std::string_view;
using std::system_error;
using std::terminate;
using std::thread;
using std::to_string;
using std::uint32_t;
using std::vector;
using types::Json;
using types::json_content_type;
using web::ConnectionInfo;
using web::Server;

namespace {

using Port = Address::Port;
using Status = http::ResponseCode::Status;

// How many connections may wait to be accepted.
constexpr int backlog = 128;

// The highest port there is.
constexpr uint32_t highest_port = 65'535;

// Where the page is, and the endpoints beneath it (HTTP API §2.3).
constexpr string_view page_path = "/config";
constexpr string_view config_api_path = "/config/api";

// Where the handler of every path outside the others is registered: the
// server's own answer to such a path is not behind the guard.
constexpr string_view every_path = "/";

// An endpoint /config serves, as GET /config/api names it.
struct Endpoint {
    string_view method;
    string_view path;
    string_view description;
};

// Each endpoint /config serves.
constexpr std::array endpoints{
    Endpoint{"GET", config_api_path, "The endpoints /config serves, and what each does"},
};

// The ports /config may listen on, in the order they are tried: port alone
// when it is named, and otherwise each port_step above main_port, up to the
// highest port. Throws invalid_argument when that leaves none.
vector<Port> ports_to_try(Port main_port, optional<Port> port) {
    if (port) {
        return {*port};
    }

    vector<Port> ports;

    for (uint32_t next = uint32_t{main_port} + Service::port_step; next <= highest_port;
         next += Service::port_step) {
        ports.push_back(static_cast<Port>(next));
    }

    if (ports.empty()) {
        throw invalid_argument("The main port, " + to_string(main_port) + ", leaves no port "
                               + to_string(Service::port_step)
                               + " above it for /config, so its port must be named");
    }

    return ports;
}

// Whether error says that a port is taken, or that this process may not
// listen on it, so that the next may be tried.
bool is_taken(const system_error& error) {
    return error.code() == errc::address_in_use || error.code() == errc::permission_denied;
}

// A server at Service::listen_host on the first of ports it can listen on.
// Throws system_error when it can listen on none, or cannot listen for any
// other reason than a port taken.
Server first_free(const vector<Port>& ports) {
    error_code last_error = std::make_error_code(errc::address_in_use);

    for (const auto port : ports) {
        try {
            return Server(Address(Service::listen_host, port));
        } catch (const system_error& error) {
            if (!is_taken(error)) {
                throw;
            }

            lWarn << "Cannot listen for /config on port" << port
                  << "so the next is tried:" << error.what();
            last_error = error.code();
        }
    }

    throw system_error(last_error, "None of the " + to_string(ports.size())
                                       + " ports tried for /config at "
                                       + string(Service::listen_host) + " can be listened on");
}

// What GET /config/api answers: each endpoint, with its method and what it
// does.
string index_of_endpoints() {
    Json::Array listed;

    for (const auto& endpoint : endpoints) {
        listed.emplace_back(Json::Object{
            {"method", Json(endpoint.method)},
            {"path", Json(endpoint.path)},
            {"description", Json(endpoint.description)},
        });
    }

    return Json(Json::Object{{"endpoints", Json(std::move(listed))}}).dump();
}

void answer_not_found(Response& response) {
    Problem(Status::NotFound, "No resource exists at this path.").answer(response);
}

// Answers request, for /config/api or a path beneath it, with index, the
// endpoints /config serves, for a GET of /config/api itself.
void answer_index(const string& index, const Request& request, Response& response) {
    const auto uri = request.uri();

    if (uri.substr(0, uri.find('?')) != config_api_path) {
        answer_not_found(response);
        return;
    }

    if ("GET" != request.method()) {
        Problem::answer_method_not_allowed(request, "GET", response);
        return;
    }

    response.set_status_code(Status::Ok);
    response.headers().set("Content-Type", string(json_content_type));
    response.set_body(index);
}

// Signs response with signer as it is sent: an answer to a HEAD goes
// without its body, so its signature covers none.
void sign_answer(const MessageSigner& signer, const Request& request, Response& response) {
    if ("HEAD" != request.method()) {
        signer.sign(response);
        return;
    }

    auto body = string(response.body());

    response.set_body("");
    signer.sign(response);
    response.set_body(std::move(body));
}

// handler, behind guard, with every answer signed by signer, the guard's
// own among them.
Server::RequestHandler guarded(const Guard& guard, const MessageSigner& signer,
                               Server::RequestHandler handler) {
    return [signer, handler = guard.in_front_of(std::move(handler))](
               const Request& request, Response& response, ConnectionInfo& connection) {
        handler(request, response, connection);
        sign_answer(signer, request, response);
    };
}

// Has server serve until it is stopped, logging why when it stops before.
void serve(Server& server) {
    try {
        server.start(backlog);
    } catch (const exception& error) {
        lErr << "The /config web server stopped:" << error;
    }
}

} // namespace

namespace config {

Service::Service(const NodeIdentity& identity, const std::filesystem::path& data_dir,
                 Port main_port, optional<Port> port, vector<string> hosts)
    : m_server(first_free(ports_to_try(main_port, port)))
    , m_local_address(m_server.local_address()) {
    const Guard guard{Credential(data_dir), std::move(hosts)};
    const MessageSigner signer(identity);

    m_server.register_handler(every_path,
                              guarded(guard, signer,
                                      [](const Request&, Response& response, ConnectionInfo&) {
        answer_not_found(response);
    }));
    m_server.register_handler(page_path, guarded(guard, signer, Page()));
    m_server.register_handler(config_api_path,
                              guarded(guard, signer,
                                      [index = index_of_endpoints()](const Request& request,
                                                                     Response& response,
                                                                     ConnectionInfo&) {
        answer_index(index, request, response);
    }));

    for (const auto& feature : NotOffered::features) {
        m_server.register_handler(feature.path, guarded(guard, signer, NotOffered(feature)));
    }

    lInfo << "/config is at http://" + string(listen_host) + ":"
                 + to_string(m_local_address.port()) + "/config/";
    m_server_thread = thread([this] {
        serve(m_server);
    });
}

Service::~Service() {
    // A thread that cannot be stopped would outlive this object, so then
    // nothing is safe but to end the program, as thread's destructor
    // would.
    try {
        m_server.stop();
        m_server_thread.join();
    } catch (const exception& error) {
        lFatal << "Could not stop the /config web server:" << error.what();
    } catch (...) {
        terminate();
    }
}

const Address& Service::local_address() const noexcept {
    return m_local_address;
}

} // namespace config
