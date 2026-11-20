#include "Server.hpp"

#include <atomic>
#include <cstddef>
#include <exception>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "http/Headers.hpp"
#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"

using http::Headers;
using http::Problem;
using http::Request;
using http::Response;
using http::ResponseCode;
using net::Address;
using net::ISocket;
using std::atomic;
using std::exception;
using std::invalid_argument;
using std::nullopt;
using std::out_of_range;
using std::runtime_error;
using std::scoped_lock;
using std::size_t;
using std::string;
using std::string_view;
using web::Server;

namespace {

void file_not_found([[maybe_unused]] const Request& request, Response& response) {
    Problem(ResponseCode::Status::NotFound, "No resource exists at this path.").answer(response);
}

// Whether the URI is under the path a handler was registered for: it starts
// with the path, and where the path stops, so does a segment of the URI. A
// handler for /data has /data, /data/, /data/x, and /data?x, but not
// /database. One for a path that ends in a slash, such as /, has every URI
// that starts with it.
bool is_under(string_view uri, string_view path) {
    if (uri.substr(0, path.size()) != path) {
        return false;
    }

    if (uri.size() == path.size() || (!path.empty() && path.back() == '/')) {
        return true;
    }

    const char next = uri.at(path.size());

    return next == '/' || next == '?';
}

string read_body(ISocket::Ptr& client, size_t bytes) {
    const auto body = client->read_exact(bytes);

    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
    return string(reinterpret_cast<const char*>(body.data()), body.size());
}

std::optional<Request> read(ISocket::Ptr& client, const atomic<bool>& running) {
    Request request;
    string line;
    string last_key;
    size_t body_length = 0;

    if (!client->read_line(line) || !request.parse_request_line(line)) {
        return nullopt;
    }

    while (running && client->read_line(line)) {
        if (line.empty()) {
            break;
        }

        try {
            last_key = Headers::process_header_line(line, last_key, request.headers());
        } catch (const runtime_error&) {
            return nullopt;
        } catch (const invalid_argument&) {
            return nullopt;
        }
    }

    const auto content_length = request.headers().get("Content-Length");

    if (content_length.has_value()) {
        try {
            body_length = std::stoull(std::string(content_length.value()));
        } catch (const invalid_argument&) {
            return nullopt;
        } catch (const out_of_range&) {
            return nullopt;
        }
    }

    request.set_body(read_body(client, body_length));
    return request;
}

// Where to connect to the listener, to wake a thread waiting in accept():
// the loopback address, for a listener bound to every address.
Address wake_address(const ISocket::Ptr& listener) {
    if (!listener) {
        throw runtime_error("Failed to bind socket on the given address.");
    }

    auto bound = listener->local_address();

    if ("0.0.0.0" == bound.host()) {
        return {"127.0.0.1", bound.port()};
    }

    if ("::" == bound.host()) {
        return {"::1", bound.port()};
    }

    return bound;
}

// A handler that throws is answered with an Internal Server Error, and the
// server goes on to the next client.
void respond(const Server::RequestHandler& handler, const Request& request, Response& response) {
    try {
        handler(request, response);
    } catch (const exception&) {
        response = Response();
        Problem(ResponseCode::Status::InternalServerError, "The request could not be answered.")
            .answer(response);
    }
}

} // namespace

namespace web {

Server::Server(Address address)
    : m_address(std::move(address))
    , m_listener(net::ISocket::bind(m_address))
    , m_wake_address(wake_address(m_listener)) {}

void Server::register_handler(string_view path, RequestHandler handler) {
    m_handlers[string(path)] = std::move(handler);
}

Address Server::local_address() const {
    return m_listener->local_address();
}

void Server::start(int backlog) {
    if (!_listen(backlog)) {
        return;
    }

    while (m_running) {
        auto client = m_listener->accept();

        if (!client || !client->is_open()) {
            continue;
        }

        if (!_serve(client.get())) {
            break;
        }

        _answer(client);

        const scoped_lock lock(m_lock);

        m_client = nullptr;
    }

    const scoped_lock lock(m_lock);

    m_listening = false;
    m_listener->close();
}

void Server::stop() {
    bool listening = false;

    {
        const scoped_lock lock(m_lock);

        m_running = false;
        listening = m_listening;

        if (nullptr != m_client) {
            m_client->shutdown();
        }
    }

    if (!listening) {
        return;
    }

    try {
        // accept() returns this connection, and start() then sees that it
        // is to stop.
        (void)ISocket::connect(m_wake_address);
        // start() may have stopped listening since, which leaves nothing to
        // wake.
        // NOLINTNEXTLINE(bugprone-empty-catch)
    } catch (const exception&) {
    }
}

Server::RequestHandler Server::_find_handler(string_view uri) const {
    const string uri_str(uri);
    const auto found_handler = m_handlers.find(uri_str);

    if (found_handler != m_handlers.end()) {
        return found_handler->second;
    }

    string best_prefix;
    RequestHandler best_handler = file_not_found;

    for (const auto& [prefix, handler] : m_handlers) {
        if (is_under(uri_str, prefix) && prefix.size() > best_prefix.size()) {
            best_prefix = prefix;
            best_handler = handler;
        }
    }

    return best_handler;
}

// Listens, unless stop() has been called. stop() wakes accept() only once
// the listener listens, so the two are ordered by m_lock.
bool Server::_listen(int backlog) {
    const scoped_lock lock(m_lock);

    if (!m_running) {
        m_listener->close();
        return false;
    }

    m_listener->listen(backlog);
    m_listening = true;
    return true;
}

// Makes client the one being answered, which stop() ends, unless stop() has
// been called.
bool Server::_serve(ISocket* client) {
    const scoped_lock lock(m_lock);

    if (!m_running) {
        return false;
    }

    m_client = client;
    return true;
}

void Server::_answer(ISocket::Ptr& client) const {
    try {
        const auto request = read(client, m_running);
        Response response;

        if (request.has_value()) {
            respond(_find_handler(request->uri()), *request, response);
        } else {
            Problem(ResponseCode::Status::BadRequest, "The request could not be read.")
                .answer(response);
        }

        client->send_all(response.to_string());
        // The client may leave, or its connection fail, before its reply is
        // sent. Only that connection ends; the server goes on.
        // NOLINTNEXTLINE(bugprone-empty-catch)
    } catch (const exception&) {
    }
}

} // namespace web