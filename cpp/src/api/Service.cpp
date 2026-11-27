#include "Service.hpp"

#include <exception>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include "NodesHandler.hpp"
#include "SeekHandler.hpp"
#include "SignatureGuard.hpp"
#include "http/Headers.hpp"
#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "http/Url.hpp"
#include "identity/KnownKeys.hpp"
#include "identity/MessageSigner.hpp"
#include "identity/MessageVerifier.hpp"
#include "identity/NodeIdentity.hpp"
#include "logger/Logger.hpp"
#include "msg/IMessage.hpp"
#include "msg/Message.hpp"
#include "net/Address.hpp"
#include "stats/Recorder.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/IDataSource.hpp"
#include "storage/SearchAnswered.hpp"
#include "storage/SearchAnswers.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"
#include "web/ConnectionInfo.hpp"
#include "web/Server.hpp"
#include "web/SiteChecks.hpp"

using api::NodesHandler;
using api::SeekHandler;
using api::Service;
using http::Headers;
using http::Problem;
using http::Request;
using http::Response;
using http::Url;
using identity::KnownKeys;
using identity::MessageSigner;
using identity::MessageVerifier;
using identity::NodeIdentity;
using msg::IMessage;
using msg::Message;
using msg::value_of;
using net::Address;
using std::exception;
using std::invalid_argument;
using std::make_shared;
using std::shared_ptr;
using std::string;
using std::string_view;
using std::terminate;
using std::thread;
using std::to_string;
using std::vector;
using storage::HashPrefix;
using storage::IDataSource;
using storage::SearchAnswered;
using storage::SearchAnswers;
using types::config_api_path;
using types::config_listen_host;
using types::config_path;
using types::Converter;
using types::max_decompressed_content_size;
using types::nodes_path;
using types::search_path_prefix;
using types::seek_path;
using web::ConnectionInfo;
using web::Server;
using web::SiteChecks;

// The thread keeping the lists takes these events, which stats::Recorder,
// SearchHandler, and peer::PeerSearch post from other files, so nothing here
// would make their messages' virtual to_string(), which value_of() casts to.
template class msg::Message<stats::Recorder::NodesUpdated>;
template class msg::Message<stats::Recorder::SeekUpdated>;
template class msg::Message<storage::SearchAnswered>;

namespace {

using NodesUpdated = stats::Recorder::NodesUpdated;
using SeekUpdated = stats::Recorder::SeekUpdated;
using Status = http::ResponseCode::Status;

// Where the data handler is registered with the server: it takes what is
// beneath it that the other handlers do not.
constexpr string_view data_path = "/data";

// The answers to searches of what store holds, kept as settings says.
// Throws invalid_argument for a ttl that is not more than zero.
shared_ptr<SearchAnswers> answers_of(const IDataSource::Ptr& store,
                                     const Service::Settings& settings) {
    return make_shared<SearchAnswers>(
        [store](const HashPrefix& prefix) { return store->starting_with(prefix); },
        settings.search_min_bits, settings.search_cache_ttl);
}

// The limits of the web server, taking no body larger than settings says.
Server::Limits limits_of(const Service::Settings& settings) {
    Server::Limits limits;

    limits.body_bytes = settings.object_bytes;
    return limits;
}

// The endpoints the node names itself by in its node list, with no external
// address or port set: localhost, meaning the address its list comes from,
// at the port it listens on (HttpApi §10.1).
vector<string> endpoints_at(const Address& local_address) {
    return {"http://localhost:" + to_string(local_address.port())};
}

// Reports what the thread keeping the lists and what peers name could not
// handle.
void log_error(const string& text) {
    lErr << text;
}

// Registers handler at path with server, with guard in front of it.
void register_guarded(Server& server, api::SignatureGuard& guard, string_view path,
                      const Server::RequestHandler& handler) {
    server.register_handler(path, guard.in_front_of(handler));
}

// The path uri names, percent-decoded, without its query.
string path_of(string_view uri) {
    return Url::decode(uri.substr(0, uri.find('?')), Url::EncodeContext::UriComponent);
}

// Whether uri names /config/api, or what is beneath it.
bool names_config_api(string_view uri) {
    const auto path = path_of(uri);
    const string api(config_api_path);

    return path == api || 0 == path.rfind(api + '/', 0);
}

// The host request's Host header names, in lower case, without its port or an
// IPv6 address's brackets. It is empty when the header names none, as when it
// is not there, or its brackets are not closed.
string host_named_by(const Request& request) {
    const auto authority = Converter::lower_case(
        Headers::trim_whitespace(request.headers().get(SiteChecks::host_header, "")));

    if (!authority.empty() && '[' == authority.front()) {
        const auto closing = authority.find(']');

        return string::npos == closing ? string() : authority.substr(1, closing - 1);
    }

    return authority.substr(0, authority.find(':'));
}

// Where request's target is on config_port, at the host its Host header
// names, or at the address /config listens at when it names none.
string config_location(const Request& request, Address::Port config_port) {
    auto host = host_named_by(request);

    if (host.empty()) {
        host = config_listen_host;
    }

    if (string::npos != host.find(':')) {
        host = "[" + host + "]";
    }

    return "http://" + host + ":" + to_string(config_port) + string(request.uri());
}

// Answers request, for a path beneath /config, on the main port. Once /config
// is served on config_port, a GET of one of its pages is sent there, so that
// an address typed or bookmarked before it moved still reaches it, and
// anything else is told where it is. With config_port 0, nothing is served
// there. No credential is asked for or looked at: a browser that sent one to
// this port would send it with every request to it (HTTP API §2.3).
void answer_moved_config(Address::Port config_port, const Request& request, Response& response) {
    if (0 == config_port) {
        Problem(Status::NotFound, "No resource exists at this path.").answer(response);
        return;
    }

    const auto location = config_location(request, config_port);

    if ("GET" == request.method() && !names_config_api(request.uri())) {
        response.set_status_code(Status::Found);
        response.headers().set("Location", location);
        return;
    }

    Problem(Status::NotFound,
            "/config is served on port " + to_string(config_port) + ", at " + location)
        .answer(response);
}

// Signs response with signer as it is sent, as SignatureGuard signs what it
// stands in front of: an answer to a HEAD goes without its body, so its
// signature covers none.
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

// Has the handler that serves the list message carries keep it.
void keep(NodesHandler& nodes_handler, SeekHandler& seek_handler, SearchAnswers& answers,
          const IMessage::Ptr& message) {
    if (const auto* const nodes = value_of<NodesUpdated>(message)) {
        nodes_handler.set_list(nodes->nodes);
    } else if (const auto* const seek = value_of<SeekUpdated>(message)) {
        seek_handler.set_list(seek->seek);
    } else if (const auto* const answered = value_of<SearchAnswered>(message)) {
        answers.add(answered->prefix, answered->results);
    } else {
        lWarn << "Not a list the web server serves:" << message->to_string();
    }
}

// Has server serve until it is stopped, logging why when it stops before.
void serve(Server& server) {
    try {
        server.start(Server::default_backlog);
    } catch (const exception& error) {
        lErr << "The web server stopped:" << error;
    }
}

} // namespace

namespace api {

Service::Service(const Address& address, const IDataSource::Ptr& store,
                 const NodeIdentity& identity, Settings settings)
    : m_data_handler(store, max_decompressed_content_size, settings.retry_after,
                     std::move(settings.is_held_off), settings.object_bytes)
    , m_seek_handler(settings.list_bytes, settings.served_list_bytes)
    , m_answers(answers_of(store, settings))
    , m_search_handler(m_answers, settings.list_bytes)
    , m_guard(MessageVerifier(make_shared<KnownKeys>(store), settings.window),
              MessageSigner(identity), settings.provisional_trust_attempts)
    , m_signer(identity)
    , m_server(address, limits_of(settings))
    , m_local_address(m_server.local_address())
    , m_own_endpoints(endpoints_at(m_local_address))
    , m_nodes_handler(identity.id(), m_own_endpoints, settings.list_bytes,
                      settings.served_list_bytes)
    , m_lists(vector<string>{NodesUpdated::name, SeekUpdated::name, SearchAnswered::name},
              [this](const IMessage::Ptr& message) {
                  keep(m_nodes_handler, m_seek_handler, *m_answers, message);
              },
              log_error) {
    register_guarded(m_server, m_guard, data_path,
                     [this](const Request& request, Response& response,
                            ConnectionInfo& connection) {
        m_data_handler(request, response, connection);
    });
    register_guarded(m_server, m_guard, nodes_path,
                     [this](const Request& request, Response& response,
                            ConnectionInfo& connection) {
        m_nodes_handler(request, response, connection);
    });
    register_guarded(m_server, m_guard, seek_path,
                     [this](const Request& request, Response& response,
                            ConnectionInfo& connection) {
        m_seek_handler(request, response, connection);
    });
    register_guarded(m_server, m_guard, search_path_prefix,
                     [this](const Request& request, Response& response,
                            ConnectionInfo& connection) {
        m_search_handler(request, response, connection);
    });
    // Not behind m_guard: anything beneath /config is answered so, whatever
    // signature it carries, or none.
    m_server.register_handler(config_path,
                              [this](const Request& request, Response& response,
                                     [[maybe_unused]] ConnectionInfo& connection) {
        answer_moved_config(m_config_port.load(), request, response);
        sign_answer(m_signer, request, response);
    });
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
        lFatal << "Could not stop the web server:" << error.what();
    } catch (...) {
        terminate();
    }
}

const Address& Service::local_address() const noexcept {
    return m_local_address;
}

const vector<string>& Service::own_endpoints() const noexcept {
    return m_own_endpoints;
}

void Service::set_config_port(Address::Port new_config_port) {
    if (0 == new_config_port) {
        throw invalid_argument("/config's port must not be 0");
    }

    m_config_port = new_config_port;
}

} // namespace api
