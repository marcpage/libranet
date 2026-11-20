#include "Server.hpp"

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <exception>
#include <iterator>
#include <list>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

#include "http/Headers.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"

using http::Headers;
using http::Request;
using http::Response;
using http::ResponseCode;
using net::Address;
using net::ISocket;
using std::byte;
using std::exception;
using std::invalid_argument;
using std::list;
using std::mutex;
using std::nullopt;
using std::optional;
using std::out_of_range;
using std::runtime_error;
using std::scoped_lock;
using std::size_t;
using std::string;
using std::string_view;
using std::system_error;
using std::thread;
using std::unique_lock;
using std::vector;
using web::Server;
using Status = http::ResponseCode::Status;

namespace {

// The header that names the request an answer answers.
constexpr auto request_path_header = "X-Request-Path";

void set_error_response(Response& response, ResponseCode::Status status) {
    const string message(ResponseCode::default_reason_phrase(status));

    response.set_status_code(status);
    response.set_body(message + "\r\n");
}

void file_not_found([[maybe_unused]] const Request& request, Response& response) {
    set_error_response(response, ResponseCode::Status::NotFound);
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

// How reading a line ended.
enum class Line : std::uint8_t {
    Read,    // At its end
    Ended,   // The connection ended first
    TooLong, // Before its end, as room ran out
};

// Reads a line, up to its '\n', leaving out each '\r'. It may take room
// bytes at most, its end included, and those it takes are taken from room.
// Reads a byte at a time, so that the next request, sent before this one is
// answered, is left for the next read.
Line read_line(ISocket& client, string& line, size_t& room) {
    vector<byte> buffer(1);

    line.clear();

    while (room > 0) {
        buffer.resize(1);

        if (!client.receive(buffer)) {
            return Line::Ended;
        }

        --room;

        const auto character = static_cast<char>(buffer.front());

        if ('\n' == character) {
            return Line::Read;
        }

        if ('\r' != character) {
            line.push_back(character);
        }
    }

    return Line::TooLong;
}

// A request read from a connection, and why it is refused, if it is. A
// refused request is answered without a handler, and its connection closed.
struct Received {
    Request request;
    bool line_read{false}; // Whether the request line was read, naming the request
    optional<Status> refusal;
};

// Reads the request's headers, after its line, into received, noting why
// they are refused if they are. Returns false if the connection ended first.
bool read_headers(ISocket& client, size_t& room, Received& received) {
    string line;
    string last_key;

    while (true) {
        const auto read = read_line(client, line, room);

        if (Line::Ended == read) {
            return false;
        }

        if (Line::TooLong == read) {
            received.refusal = Status::RequestHeaderFieldsTooLarge;
            return true;
        }

        if (line.empty()) {
            return true;
        }

        try {
            last_key = Headers::process_header_line(line, last_key, received.request.headers());
        } catch (const runtime_error&) {
            received.refusal = Status::BadRequest;
            return true;
        } catch (const invalid_argument&) {
            received.refusal = Status::BadRequest;
            return true;
        }
    }
}

// The length of the body the headers give, or why the request is refused:
// a length that is not digits alone (RFC 9110 §8.6), or one larger than
// most_bytes. A body sent in chunks, or otherwise encoded, is not taken, as
// where it ends would not be known (RFC 9112 §6.1).
optional<Status> body_length(const Headers& headers, size_t most_bytes, size_t& length) {
    length = 0;

    if (headers.has("Transfer-Encoding")) {
        return Status::NotImplemented;
    }

    const auto value = headers.get("Content-Length");

    if (!value.has_value()) {
        return nullopt;
    }

    if (value->empty() || string_view::npos != value->find_first_not_of("0123456789")) {
        return Status::BadRequest;
    }

    try {
        length = std::stoull(string(*value));
    } catch (const out_of_range&) {
        return Status::PayloadTooLarge;
    }

    if (length > most_bytes) {
        return Status::PayloadTooLarge;
    }

    return nullopt;
}

// The next request on the connection, or nothing if the connection ended
// before it was all sent, as a client's does once it is done.
optional<Received> read_request(ISocket& client, const Server::Limits& limits) {
    Received received;
    string line;
    size_t room = limits.head_bytes;
    const auto read = read_line(client, line, room);

    if (Line::Ended == read) {
        return nullopt;
    }

    if (Line::TooLong == read) {
        received.refusal = Status::UriTooLong;
        return received;
    }

    if (!received.request.parse_request_line(line)) {
        received.refusal = Status::BadRequest;
        return received;
    }

    received.line_read = true;

    if (!read_headers(client, room, received)) {
        return nullopt;
    }

    size_t length = 0;

    if (!received.refusal.has_value()) {
        received.refusal = body_length(received.request.headers(), limits.body_bytes, length);
    }

    if (received.refusal.has_value()) {
        return received;
    }

    const auto body = client.read_exact(length);

    if (body.size() != length) {
        return nullopt;
    }

    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
    received.request.set_body(string(reinterpret_cast<const char*>(body.data()), body.size()));
    return received;
}

bool header_equals(std::string_view left, std::string_view right) {
    const Headers::CaseInsensitiveLess less;

    // NOLINTNEXTLINE(readability-suspicious-call-argument)
    return !less(left, right) && !less(right, left);
}

// Whether the headers' Connection names option, as one of a list.
bool has_connection_option(const Headers& headers, string_view option) {
    auto options = headers.get("Connection", "");

    while (!options.empty()) {
        const auto comma = options.find(',');
        const auto named = Headers::trim_whitespace(options.substr(0, comma));

        if (header_equals(named, option)) {
            return true;
        }

        options = string_view::npos == comma ? string_view() : options.substr(comma + 1);
    }

    return false;
}

// Whether the connection is closed once the request is answered with the
// response (RFC 9112 §9.3): when either says Connection: close, when the
// request is HTTP/1.0, which closes by default, and when it was refused.
bool closes(const Received& received, const Response& response) {
    return received.refusal.has_value()
           || has_connection_option(received.request.headers(), "close")
           || has_connection_option(response.headers(), "close")
           || "HTTP/1.0" == received.request.version();
}

// Whether an answer with the code has no body (RFC 9110 §6.4.1).
bool is_bodiless(const ResponseCode& code) {
    return code.is_informational() || code == Status::NoContent || code == Status::NotModified;
}

// Readies the response to be sent: naming its request, giving its body's
// length, saying whether the connection closes, and dropping its body when
// none is sent. The answer to a HEAD gives the length its handler's body
// has, as a GET's would.
void make_ready(Response& response, const Received& received, bool closing) {
    auto& headers = response.headers();

    if (received.line_read) {
        headers.set(request_path_header, string(received.request.uri()));
    }

    if (closing) {
        headers.set("Connection", "close");
    }

    if (is_bodiless(response.code())) {
        headers.remove("Content-Length");
        response.set_body("");
        return;
    }

    headers.set("Content-Length", std::to_string(response.body().size()));

    if ("HEAD" == received.request.method()) {
        response.set_body("");
    }
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
// server goes on to the next request.
void respond(const Server::RequestHandler& handler, const Request& request, Response& response) {
    try {
        handler(request, response);
    } catch (const exception&) {
        response = Response();
        set_error_response(response, ResponseCode::Status::InternalServerError);
    }
}

// Ends the connection, so that the thread serving it stops reading or
// writing it. A client that has gone has ended it already.
void end(ISocket& client) noexcept {
    try {
        client.shutdown();
        // NOLINTNEXTLINE(bugprone-empty-catch)
    } catch (const exception&) {
    }
}

Server::Limits checked(const Server::Limits& limits) {
    if (0 == limits.connections) {
        throw invalid_argument("A server must take at least one connection.");
    }

    return limits;
}

} // namespace

namespace web {

Server::Server(Address address)
    : Server(std::move(address), Limits{}) {}

Server::Server(Address address, const Limits& limits)
    : m_address(std::move(address))
    , m_limits(checked(limits))
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

    try {
        while (_wait_for_room()) {
            _serve(m_listener->accept());
        }
    } catch (...) {
        _end_all();
        throw;
    }

    _end_all();
}

void Server::stop() {
    bool listening = false;

    {
        const scoped_lock lock(m_lock);

        m_running = false;
        listening = m_listening;

        for (const auto& connection : m_connections) {
            if (connection.socket) {
                end(*connection.socket);
            }
        }

        m_changed.notify_all();
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

// Waits until fewer connections are served than the limit, and joins the
// threads of those that have closed. Returns false once stop() has been
// called.
bool Server::_wait_for_room() {
    list<Connection> closed;
    bool running = false;

    {
        unique_lock<mutex> lock(m_lock);

        m_changed.wait(lock, [this] {
            const auto open = std::count_if(m_connections.begin(), m_connections.end(),
                                            [](const Connection& connection) {
                return nullptr != connection.socket;
            });

            return !m_running || static_cast<size_t>(open) < m_limits.connections;
        });
        running = m_running;

        for (auto connection = m_connections.begin(); connection != m_connections.end();) {
            const auto next = std::next(connection);

            if (!connection->socket) {
                closed.splice(closed.end(), m_connections, connection);
            }

            connection = next;
        }
    }

    for (auto& connection : closed) {
        connection.thread.join();
    }

    return running;
}

// Serves the client on a thread of its own, unless stop() has been called,
// or the client cannot be served, which closes its connection.
void Server::_serve(ISocket::Ptr client) {
    if (!client || !client->is_open()) {
        return;
    }

    try {
        client->set_timeout(m_limits.idle);
        // The client may have gone already.
    } catch (const system_error&) {
        return;
    }

    const scoped_lock lock(m_lock);

    if (!m_running) {
        return;
    }

    auto& connection = m_connections.emplace_back();

    connection.socket = std::move(client);

    try {
        connection.thread = thread([this, &connection] {
            _converse(connection);
        });
    } catch (const system_error&) {
        // No thread is left to serve it, which ends only this connection.
        m_connections.pop_back();
    }
}

// Answers the requests on the connection, one at a time in the order they
// came, until it is to close, and then closes it.
void Server::_converse(Connection& connection) {
    try {
        // Only this thread changes connection.socket, so it reads it
        // unguarded.
        auto& client = *connection.socket;

        while (m_running && _answer(client)) {
        }
        // The client may go, send nothing for too long, or its connection
        // fail. Only that connection ends; the server goes on.
        // NOLINTNEXTLINE(bugprone-empty-catch)
    } catch (const exception&) {
    }

    const scoped_lock lock(m_lock);

    connection.socket.reset();
    m_changed.notify_all();
}

// Reads the next request on the connection and answers it. Returns whether
// the connection stays open for another.
bool Server::_answer(ISocket& client) const {
    const auto received = read_request(client, m_limits);

    if (!received.has_value()) {
        return false;
    }

    Response response;

    if (received->refusal.has_value()) {
        set_error_response(response, *received->refusal);
    } else {
        respond(_find_handler(received->request.uri()), received->request, response);
    }

    const auto closing = closes(*received, response);

    make_ready(response, *received, closing);
    client.send_all(response.to_string());
    return !closing;
}

// Stops listening, once start() is to return, and ends every connection,
// waiting for the threads serving them.
void Server::_end_all() {
    list<Connection> connections;

    {
        const scoped_lock lock(m_lock);

        m_listening = false;

        for (const auto& connection : m_connections) {
            if (connection.socket) {
                end(*connection.socket);
            }
        }

        connections.splice(connections.end(), m_connections);
    }

    for (auto& connection : connections) {
        connection.thread.join();
    }

    m_listener->close();
}

} // namespace web
