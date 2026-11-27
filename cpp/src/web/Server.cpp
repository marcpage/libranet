#include "Server.hpp"

#include <algorithm>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <exception>
#include <iterator>
#include <list>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

#include "ConnectionInfo.hpp"
#include "http/Headers.hpp"
#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "io/BufferedReader.hpp"
#include "io/MeteredReader.hpp"
#include "io/SocketReader.hpp"
#include "logger/Logger.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"

using http::Headers;
using http::Problem;
using http::Request;
using http::Response;
using http::ResponseCode;
using io::BufferedReader;
using io::LimitExceeded;
using io::MeteredReader;
using io::SocketReader;
using net::Address;
using net::ISocket;
using std::exception;
using std::invalid_argument;
using std::list;
using std::mutex;
using std::nullopt;
using std::optional;
using std::out_of_range;
using std::pair;
using std::runtime_error;
using std::scoped_lock;
using std::size_t;
using std::string;
using std::string_view;
using std::system_error;
using std::thread;
using std::unique_lock;
using std::unordered_map;
using std::vector;
using web::ConnectionInfo;
using web::Server;
using Status = http::ResponseCode::Status;

namespace {

using Handlers = unordered_map<string, Server::RequestHandler>;
// Header names, each with its value.
using HeaderValues = vector<pair<string, string>>;

// The header that names the request an answer answers.
constexpr auto request_path_header = "X-Request-Path";

void file_not_found([[maybe_unused]] const Request& request, Response& response,
                    [[maybe_unused]] ConnectionInfo& connection) {
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

// How reading a line ended.
enum class Line : std::uint8_t {
    Read,    // At its end
    Ended,   // The connection ended first
    TooLong, // Before its end, as the limit ran out
};

// Reads a line, up to its '\n', leaving out each '\r'. It takes no more
// than the reader's limit has left, its end included.
Line read_line(MeteredReader& reader, string& line) {
    try {
        line = reader.read_until('\n');
    } catch (const LimitExceeded&) {
        return Line::TooLong;
    }

    if (line.empty() || '\n' != line.back()) {
        return Line::Ended;
    }

    line.pop_back();
    line.erase(std::remove(line.begin(), line.end(), '\r'), line.end());
    return Line::Read;
}

// A request read from a connection, and why it is refused, if it is. A
// refused request is answered with the problem, without a handler, and its
// connection closed.
struct Received {
    Request request;
    bool line_read{false}; // Whether the request line was read, naming the request
    optional<Problem> refusal;
};

// Why a request whose line, headers, or length are not HTTP is refused.
Problem unreadable() {
    return {Status::BadRequest, "The request could not be read."};
}

// Why a request whose body is larger than most_bytes is refused (HTTP API
// §17.2).
Problem too_large(size_t most_bytes) {
    // Mistaken: the problem keeps its own copy of the detail, not a view of
    // the temporary. cppcheck loses Problem's constructors in a file that
    // names it with `using http::Problem;` (see Problem.hpp).
    // cppcheck-suppress returnDanglingLifetime
    return {Problem::Type::ContentTooLarge, Status::PayloadTooLarge,
            "The request's body is larger than " + std::to_string(most_bytes)
                + " bytes, the most the server takes."};
}

// Reads the request's headers, after its line, into received, noting why
// they are refused if they are. Returns false if the connection ended first.
bool read_headers(MeteredReader& reader, Received& received) {
    string line;
    string last_key;

    while (true) {
        const auto read = read_line(reader, line);

        if (Line::Ended == read) {
            return false;
        }

        if (Line::TooLong == read) {
            received.refusal = Problem(Status::RequestHeaderFieldsTooLarge,
                                       "The request's headers are longer than the server reads.");
            return true;
        }

        if (line.empty()) {
            return true;
        }

        try {
            last_key = Headers::process_header_line(line, last_key, received.request.headers());
        } catch (const runtime_error&) {
            received.refusal = unreadable();
            return true;
        } catch (const invalid_argument&) {
            received.refusal = unreadable();
            return true;
        }
    }
}

// The length of the body the headers give, or why the request is refused:
// a length that is not digits alone (RFC 9110 §8.6), or one larger than
// most_bytes. A body sent in chunks, or otherwise encoded, is not taken, as
// where it ends would not be known (RFC 9112 §6.1).
optional<Problem> body_length(const Headers& headers, size_t most_bytes, size_t& length) {
    length = 0;

    if (headers.has("Transfer-Encoding")) {
        return Problem(Status::NotImplemented,
                       "A body sent with a Transfer-Encoding cannot be read.");
    }

    const auto value = headers.get("Content-Length");

    if (!value.has_value()) {
        return nullopt;
    }

    if (value->empty() || string_view::npos != value->find_first_not_of("0123456789")) {
        return unreadable();
    }

    try {
        length = std::stoull(string(*value));
    } catch (const out_of_range&) {
        return too_large(most_bytes);
    }

    if (length > most_bytes) {
        return too_large(most_bytes);
    }

    return nullopt;
}

// The next request on the connection, or nothing if the connection ended
// before it was all sent, as a client's does once it is done. Its line and
// headers may take limits.head_bytes, and its body the length it gives.
optional<Received> read_request(MeteredReader& reader, const Server::Limits& limits) {
    Received received;
    string line;

    reader.reset(limits.head_bytes);

    const auto read = read_line(reader, line);

    if (Line::Ended == read) {
        return nullopt;
    }

    if (Line::TooLong == read) {
        received.refusal = Problem(Status::UriTooLong,
                                   "The request line is longer than the server reads.");
        return received;
    }

    if (!received.request.parse_request_line(line)) {
        received.refusal = unreadable();
        return received;
    }

    received.line_read = true;

    if (!read_headers(reader, received)) {
        return nullopt;
    }

    size_t length = 0;

    if (!received.refusal.has_value()) {
        received.refusal = body_length(received.request.headers(), limits.body_bytes, length);
    }

    if (received.refusal.has_value()) {
        return received;
    }

    reader.reset(length);

    const auto body = reader.read_exact(length);

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

// Whether the headers' ConnectionInfo names option, as one of a list.
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
// response (RFC 9112 §9.3): when either says ConnectionInfo: close, when the
// request is HTTP/1.0, which closes by default, when it was refused, and when
// its handler had the connection closed.
bool closes(const Received& received, const Response& response, const ConnectionInfo& connection) {
    return received.refusal.has_value() || connection.is_closing()
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

// A handler that throws is answered with an Internal Server Error, and
// logged, and the server goes on to the next request.
void respond(const Server::RequestHandler& handler, const Request& request, Response& response,
             ConnectionInfo& connection) {
    try {
        handler(request, response, connection);
    } catch (const exception& error) {
        lErr << "Could not answer" << request.method() << request.uri() << "from"
             << connection.remote_address().to_string() + ":" << error.what();
        response = Response();
        Problem(ResponseCode::Status::InternalServerError, "The request could not be answered.")
            .answer(response);
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

// The handler in handlers, by path, that has uri: the one at uri itself, or
// else the one at the longest path uri is under; file_not_found when none has
// it.
Server::RequestHandler handler_for(const Handlers& handlers, string_view uri) {
    const string uri_str(uri);
    const auto found_handler = handlers.find(uri_str);

    if (found_handler != handlers.end()) {
        return found_handler->second;
    }

    string best_prefix;
    Server::RequestHandler best_handler = file_not_found;

    for (const auto& [prefix, handler] : handlers) {
        if (is_under(uri_str, prefix) && prefix.size() > best_prefix.size()) {
            best_prefix = prefix;
            best_handler = handler;
        }
    }

    return best_handler;
}

// Reads the next request on the connection, from the reader, within limits,
// and answers it with the handler in handlers that has it, carrying each of
// every_answer. Returns whether the connection stays open for another.
bool answer_next(const Server::Limits& limits, const Handlers& handlers,
                 const HeaderValues& every_answer, ISocket& client, MeteredReader& reader,
                 ConnectionInfo& connection) {
    const auto received = read_request(reader, limits);

    if (!received.has_value()) {
        return false;
    }

    connection.add_a_request();

    Response response;

    if (received->refusal.has_value()) {
        received->refusal->answer(response);
    } else {
        respond(handler_for(handlers, received->request.uri()), received->request, response,
                connection);
    }

    for (const auto& [name, value] : every_answer) {
        response.headers().set(name, value);
    }

    const auto closing = closes(*received, response, connection);

    make_ready(response, *received, closing);
    client.send_all(response.to_string());
    return !closing;
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

void Server::set_on_every_answer(string name, string value) {
    m_every_answer.emplace_back(std::move(name), std::move(value));
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
        const scoped_lock lock(m_mutex);

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

// Listens, unless stop() has been called. stop() wakes accept() only once
// the listener listens, so the two are ordered by m_mutex.
bool Server::_listen(int backlog) {
    const scoped_lock lock(m_mutex);

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
    list<Client> closed;
    bool running = false;

    {
        unique_lock<mutex> lock(m_mutex);

        m_changed.wait(lock, [this] {
            const auto open = std::count_if(m_connections.begin(), m_connections.end(),
                                            [](const Client& connection) {
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

    const scoped_lock lock(m_mutex);

    if (!m_running) {
        return;
    }

    auto& connection = m_connections.emplace_back();

    connection.socket = std::move(client);

    try {
        connection.thread = thread([this, &connection] {
            _converse(connection);
        });
    } catch (const system_error& error) {
        // No thread is left to serve it, which ends only this connection.
        lWarn << "Could not start a thread to serve a connection:" << error.what();
        m_connections.pop_back();
    }
}

// Answers the requests on the client's connection, one at a time in the
// order they came, until it is to close, and then closes it.
void Server::_converse(Client& client) {
    try {
        // Only this thread changes client.socket, so it reads it unguarded.
        auto& socket = *client.socket;
        ConnectionInfo connection(socket.remote_address(), socket.local_address());
        // One reader for the connection, whose buffer keeps what was sent
        // after one request, as the next, for the next read.
        auto buffered = std::make_unique<BufferedReader>(std::make_unique<SocketReader>(socket));
        MeteredReader reader(std::move(buffered), m_limits.head_bytes);

        while (m_running
               && answer_next(m_limits, m_handlers, m_every_answer, socket, reader, connection)) {
        }
        // The client may go, send nothing for too long, or its connection
        // fail. Only that connection ends; the server goes on.
    } catch (const exception& error) {
        lDebug << "Stopped serving a connection:" << error.what();
    }

    const scoped_lock lock(m_mutex);

    client.socket.reset();
    m_changed.notify_all();
}

// Stops listening, once start() is to return, and ends every connection,
// waiting for the threads serving them.
void Server::_end_all() {
    list<Client> connections;

    {
        const scoped_lock lock(m_mutex);

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
