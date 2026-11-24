#include "Service.hpp"

#include <exception>
#include <memory>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include "NodesHandler.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
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
#include "web/ConnectionInfo.hpp"
#include "web/Server.hpp"

using api::NodesHandler;
using api::Service;
using http::Request;
using http::Response;
using identity::KnownKeys;
using identity::MessageSigner;
using identity::MessageVerifier;
using identity::NodeIdentity;
using msg::IMessage;
using msg::Message;
using msg::value_of;
using net::Address;
using std::exception;
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
using web::ConnectionInfo;
using web::Server;

// The thread keeping the lists takes these events, which stats::Recorder,
// SearchHandler, and peer::PeerSearch post from other files, so nothing here
// would make their messages' virtual to_string(), which value_of() casts to.
template class msg::Message<stats::Recorder::NodesUpdated>;
template class msg::Message<stats::Recorder::SeekUpdated>;
template class msg::Message<storage::SearchAnswered>;

namespace {

using NodesUpdated = stats::Recorder::NodesUpdated;
using SeekUpdated = stats::Recorder::SeekUpdated;

// Where the handlers other than NodesHandler are registered with the server.
constexpr string_view data_path = "/data";
constexpr string_view seek_path = "/data/seek";
constexpr string_view search_path = "/data/search";

// The answers to searches of what store holds, kept as settings says.
// Throws invalid_argument for a ttl that is not more than zero.
shared_ptr<SearchAnswers> answers_of(const IDataSource::Ptr& store,
                                     const Service::Settings& settings) {
    return make_shared<SearchAnswers>(
        [store](const HashPrefix& prefix) { return store->starting_with(prefix); },
        settings.search_min_bits, settings.search_cache_ttl);
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

} // namespace

namespace api {

Service::Service(const Address& address, const IDataSource::Ptr& store,
                 const NodeIdentity& identity, Settings settings)
    : m_data_handler(store, DataHandler::default_content_bytes, settings.retry_after,
                     std::move(settings.is_held_off))
    , m_answers(answers_of(store, settings))
    , m_search_handler(m_answers)
    , m_guard(MessageVerifier(make_shared<KnownKeys>(store), settings.window),
              MessageSigner(identity), settings.provisional_trust_attempts)
    , m_server(address)
    , m_local_address(m_server.local_address())
    , m_own_endpoints(endpoints_at(m_local_address))
    , m_nodes_handler(identity.id(), m_own_endpoints)
    , m_lists(vector<string>{NodesUpdated::name, SeekUpdated::name, SearchAnswered::name},
              [this](const IMessage::Ptr& message) { _keep(message); }, log_error) {
    _register(data_path, [this](const Request& request, Response& response,
                                ConnectionInfo& connection) {
        m_data_handler(request, response, connection);
    });
    _register(NodesHandler::path, [this](const Request& request, Response& response,
                                         ConnectionInfo& connection) {
        m_nodes_handler(request, response, connection);
    });
    _register(seek_path, [this](const Request& request, Response& response,
                                ConnectionInfo& connection) {
        m_seek_handler(request, response, connection);
    });
    _register(search_path, [this](const Request& request, Response& response,
                                  ConnectionInfo& connection) {
        m_search_handler(request, response, connection);
    });
    m_server_thread = thread([this] {
        _serve();
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

// Registers handler at path, with the guard in front of it.
void Service::_register(string_view path, const Server::RequestHandler& handler) {
    m_server.register_handler(path, m_guard.in_front_of(handler));
}

void Service::_keep(const IMessage::Ptr& message) {
    if (const auto* const nodes = value_of<NodesUpdated>(message)) {
        m_nodes_handler.set_nodes(nodes->nodes);
    } else if (const auto* const seek = value_of<SeekUpdated>(message)) {
        m_seek_handler.set_list(seek->seek);
    } else if (const auto* const answered = value_of<SearchAnswered>(message)) {
        m_answers->add(answered->prefix, answered->results);
    } else {
        lWarn << "Not a list the web server serves:" << message->to_string();
    }
}

void Service::_serve() {
    try {
        m_server.start(backlog);
    } catch (const exception& error) {
        lErr << "The web server stopped:" << error;
    }
}

} // namespace api
