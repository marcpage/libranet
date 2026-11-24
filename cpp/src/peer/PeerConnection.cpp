#include "PeerConnection.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstddef>
#include <deque>
#include <exception>
#include <functional>
#include <future>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <utility>

#include "http/Headers.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "io/BufferedReader.hpp"
#include "io/IReader.hpp"
#include "io/MeteredReader.hpp"
#include "io/SocketReader.hpp"
#include "logger/Logger.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"

using http::Headers;
using http::Request;
using http::Response;
using io::BufferedReader;
using io::IReader;
using io::MeteredReader;
using io::SocketReader;
using net::Address;
using net::ISocket;
using peer::PeerConnection;
using std::exception_ptr;
using std::future;
using std::invalid_argument;
using std::make_exception_ptr;
using std::nullopt;
using std::optional;
using std::promise;
using std::scoped_lock;
using std::size_t;
using std::string;
using std::string_view;
using std::system_error;
using std::thread;
using std::to_string;
using std::unique_lock;
using Status = http::ResponseCode::Status;

namespace {

// What ends each line of a request's head (RFC 9112 §2.1).
constexpr string_view line_end = "\r\n";

// limits, once checked. Throws invalid_argument, naming the setting, for a
// timeout that is not positive, as the Python node refuses one.
PeerConnection::Limits checked(const PeerConnection::Limits& limits) {
    if (limits.connect_timeout.count() <= 0) {
        throw invalid_argument("peers.connect_timeout_seconds is not positive: "
                               + to_string(limits.connect_timeout.count()) + " ms");
    }

    if (limits.request_timeout.count() <= 0) {
        throw invalid_argument("peers.request_timeout_seconds is not positive: "
                               + to_string(limits.request_timeout.count()) + " ms");
    }

    return limits;
}

// Whether text is a token, as a method or a header's name is (RFC 9110
// §5.6.2).
bool is_token(string_view text) {
    constexpr string_view symbols = "!#$%&'*+-.^_`|~";

    return !text.empty() && std::all_of(text.begin(), text.end(), [symbols](char character) {
        return 0 != std::isalnum(static_cast<unsigned char>(character))
               || string_view::npos != symbols.find(character);
    });
}

// Whether target is a path, and perhaps a query, of visible characters
// (RFC 9112 §3.2.1).
bool is_origin_form(string_view target) {
    return !target.empty() && '/' == target.front()
           && std::all_of(target.begin(), target.end(), [](char character) {
        return '!' <= character && character <= '~';
    });
}

// Whether each of the headers writes as a line of its own: a name that is a
// token, a colon, and a value with no line break in it.
bool writes_whole(const Headers& headers) {
    const auto text = headers.to_string();
    string_view rest(text);
    size_t lines = 0;

    // The last line, which is empty, ends the headers.
    while (rest.size() > line_end.size()) {
        const auto end = rest.find(line_end);
        const auto line = rest.substr(0, end);

        if (string_view::npos == end || !is_token(line.substr(0, line.find(':')))
            || string_view::npos != line.find_first_of("\r\n")) {
            return false;
        }

        rest.remove_prefix(end + line_end.size());
        ++lines;
    }

    return lines == headers.size();
}

// Whether a request with the method has a body, even an empty one, whose
// length is given (RFC 9110 §8.6).
bool has_body(string_view method) {
    return "POST" == method || "PUT" == method || "PATCH" == method;
}

// The request as text to send: as HTTP/1.1, with Host naming the node at
// address, and the length of its body, where it has one or its method
// expects one. Throws invalid_argument for a request whose answer could
// not be told from the next one's.
string framed(Request request, const Address& address) {
    auto& headers = request.headers();
    const auto method = request.method();

    if (!is_token(method) || !is_origin_form(request.uri())) {
        throw invalid_argument("Not a request line: " + string(method) + " "
                               + string(request.uri()));
    }

    if ("HEAD" == method) {
        throw invalid_argument("The answer to a HEAD gives a length it does not send");
    }

    if (headers.has("Transfer-Encoding")) {
        throw invalid_argument("A request is not sent with a Transfer-Encoding");
    }

    headers.set("Host", address.to_string());
    headers.remove("Content-Length");

    if (!request.body().empty() || has_body(method)) {
        headers.set("Content-Length", to_string(request.body().size()));
    }

    if (!writes_whole(headers)) {
        throw invalid_argument("A header of the request is not a line of its own");
    }

    request.set_version("HTTP/1.1");
    return request.to_string();
}

// Whether the headers' Connection names close, as one of a list of options
// (RFC 9112 §9.6).
bool says_close(const Headers& headers) {
    constexpr string_view close = "close";
    const Headers::CaseInsensitiveLess less;
    auto options = headers.get("Connection", "");

    while (!options.empty()) {
        const auto comma = options.find(',');
        const auto option = Headers::trim_whitespace(options.substr(0, comma));

        if (!less(option, close) && !less(close, option)) {
            return true;
        }

        options = string_view::npos == comma ? string_view() : options.substr(comma + 1);
    }

    return false;
}

// The next final answer, past the interim (1xx) ones before it (RFC 9110
// §15.2), or nothing once the connection ends. Throws invalid_argument for
// a switch to another protocol, which nothing asked for, and what
// Response::read() throws.
optional<Response> read_final(MeteredReader& reader, const Response::Limits& limits) {
    auto answer = Response::read(reader, limits);

    while (answer && answer->code().is_informational()) {
        if (answer->code() == Status::SwitchingProtocols) {
            throw invalid_argument("The node switched protocols, which nothing asked for");
        }

        answer = Response::read(reader, limits);
    }

    return answer;
}

// Whether a socket's call failed only as its timeout passed. EAGAIN and
// EWOULDBLOCK are the same error on Linux and macOS.
bool is_timeout(const system_error& error) {
    return error.code() == std::errc::resource_unavailable_try_again;
}

// What failure says went wrong.
string reason(const exception_ptr& failure) {
    string what;

    try {
        std::rethrow_exception(failure);
    } catch (const std::exception& error) {
        what = error.what();
    }

    return what;
}

// Reads with a function, as a connection reads with a method of its own.
class FunctionReader final : public IReader {
public:
    using Read = std::function<optional<size_t>(std::byte* data, size_t size)>;

    explicit FunctionReader(Read read)
        : m_read(std::move(read)) {}

    [[nodiscard]] optional<size_t> read_some(std::byte* data, size_t size) override {
        return m_read(data, size);
    }

private:
    Read m_read;
};

// Ends the connection on socket both ways, so that a thread sending or
// reading it stops.
void end_connection(ISocket& socket) noexcept {
    try {
        socket.shutdown();
        // The node may have ended it already.
        // NOLINTNEXTLINE(bugprone-empty-catch)
    } catch (const std::exception&) {
    }
}

} // namespace

namespace peer {

PeerConnection::PeerConnection(Address address, const Limits& limits)
    : m_address(std::move(address))
    , m_limits(checked(limits))
    , m_socket(ISocket::connect(m_address, m_limits.connect_timeout)) {
    // A request takes no longer to send than its answer may take to come.
    m_socket->set_send_timeout(m_limits.request_timeout);
    m_sender = thread([this] {
        _send_all();
    });

    try {
        m_reader = thread([this] {
            _read_all();
        });
    } catch (const system_error&) {
        close();
        throw;
    }
}

PeerConnection::~PeerConnection() {
    // Threads that cannot be joined would outlive this object, so then
    // nothing is safe but to end the program, as their destructors would.
    try {
        close();
    } catch (const std::exception& error) {
        lFatal << "Could not close a connection to a peer:" << error.what();
    } catch (...) {
        std::terminate();
    }
}

future<Response> PeerConnection::send(Request request) {
    auto text = framed(std::move(request), m_address);
    promise<Response> answer;
    auto answered = answer.get_future();

    {
        const scoped_lock lock(m_mutex);

        if (m_closed) {
            throw Closed("The connection to " + m_address.to_string() + " is closed");
        }

        // The node has the request timeout from now to make progress.
        if (m_waiting.empty()) {
            m_last_progress = Clock::now();
        }

        m_waiting.push_back(std::move(answer));
        m_unsent.push_back(std::move(text));
    }

    m_sending.notify_one();
    return answered;
}

const Address& PeerConnection::address() const noexcept {
    return m_address;
}

bool PeerConnection::is_open() const {
    const scoped_lock lock(m_mutex);

    return !m_closed;
}

bool PeerConnection::was_turned_away() const {
    const scoped_lock lock(m_mutex);

    return m_turned_away;
}

void PeerConnection::close() {
    const scoped_lock joining(m_joining);

    _close_for(make_exception_ptr(
        Closed("The connection to " + m_address.to_string() + " was closed")));
    end_connection(*m_socket);

    if (m_sender.joinable()) {
        m_sender.join();
    }

    if (m_reader.joinable()) {
        m_reader.join();
    }
}

// Sends each request queued, in turn, until the connection closes. A send
// that fails closes it; the reader still reads the answers the node sent to
// what was sent before, until the node closes it too, or makes no progress.
void PeerConnection::_send_all() {
    try {
        while (const auto text = _next_to_send()) {
            m_socket->send_all(*text);
            _progressed();
        }
    } catch (const std::exception& error) {
        lDebug << "Could not send to" << m_address.to_string() + ":" << error.what();
        _close_for(std::current_exception());
    }
}

// The next request to send, once one is queued, or nothing once the
// connection has closed.
optional<string> PeerConnection::_next_to_send() {
    unique_lock<std::mutex> lock(m_mutex);

    m_sending.wait(lock, [this] {
        return m_closed || !m_unsent.empty();
    });

    if (m_closed) {
        return nullopt;
    }

    auto text = std::move(m_unsent.front());

    m_unsent.pop_front();
    return text;
}

// Reads the answers, giving each to its request, until the connection is
// to close; then closes it, and fails the requests still waiting.
void PeerConnection::_read_all() {
    try {
        auto receiver = std::make_unique<FunctionReader>([this](std::byte* data, size_t size) {
            return _receive(data, size);
        });
        // One reader for the connection, whose buffer keeps what was sent
        // after one answer, as the next, for the next read.
        MeteredReader reader(std::make_unique<BufferedReader>(std::move(receiver)),
                             m_limits.answer.head_bytes);

        while (_read_answer(reader)) {
        }

        _close_for(make_exception_ptr(Closed(m_address.to_string() + " closed the connection")));
    } catch (const std::exception& error) {
        lDebug << "Could not read from" << m_address.to_string() + ":" << error.what();
        _close_for(std::current_exception());
    }

    end_connection(*m_socket);
    _fail_waiting();
}

// Reads the next answer, and gives it to the oldest request waiting.
// Returns false once the connection is to close: the node closed it, or
// said it would. Throws invalid_argument for an answer nothing asked for,
// and what read_final() throws.
bool PeerConnection::_read_answer(MeteredReader& reader) {
    auto answer = read_final(reader, m_limits.answer);

    if (!answer) {
        return false;
    }

    auto waiting = _take_oldest();
    const auto closing = says_close(answer->headers());

    // Closed before the answer is given, so that its request sees it so.
    if (closing) {
        _close_for(make_exception_ptr(Closed(m_address.to_string() + " turned this node away")),
                   true);
    }

    waiting.set_value(std::move(*answer));
    return !closing;
}

// Reads what the node sends next into data, up to size bytes, or nothing
// once it has closed the connection. While no request waits, it waits as
// long as the node sends nothing; while one does, it throws system_error
// once the node has made no progress for the request timeout.
optional<size_t> PeerConnection::_receive(std::byte* data, size_t size) {
    SocketReader socket(*m_socket);

    while (true) {
        m_socket->set_receive_timeout(_time_left());

        try {
            const auto read = socket.read_some(data, size);

            _progressed();
            return read;
        } catch (const system_error& error) {
            if (!is_timeout(error)) {
                throw;
            }
        }
    }
}

// How long to wait for the node to send something: until the oldest
// request waiting times out, or, while none waits, as long as that would
// take, after which it waits again. Throws system_error once one has timed
// out.
PeerConnection::Milliseconds PeerConnection::_time_left() const {
    const scoped_lock lock(m_mutex);

    if (m_waiting.empty()) {
        return m_limits.request_timeout;
    }

    const auto waited = std::chrono::floor<Milliseconds>(Clock::now() - m_last_progress);
    const auto left = m_limits.request_timeout - waited;

    if (left.count() <= 0) {
        throw system_error(std::make_error_code(std::errc::timed_out),
                           m_address.to_string() + " made no progress for "
                               + to_string(m_limits.request_timeout.count()) + " ms");
    }

    return left;
}

// Notes that the node took a request, or sent something.
void PeerConnection::_progressed() {
    const scoped_lock lock(m_mutex);

    m_last_progress = Clock::now();
}

// The promise of the oldest request waiting. Throws invalid_argument where
// none waits, for an answer nothing asked for.
promise<Response> PeerConnection::_take_oldest() {
    const scoped_lock lock(m_mutex);

    if (m_waiting.empty()) {
        throw invalid_argument(m_address.to_string() + " sent an answer nothing asked for");
    }

    auto oldest = std::move(m_waiting.front());

    m_waiting.pop_front();
    return oldest;
}

// Closes the connection for failure, unless it has closed already: no
// request is taken or sent after it, and those waiting fail once the reader
// has stopped. turned_away says the node closed it after answering.
void PeerConnection::_close_for(const exception_ptr& failure, bool turned_away) {
    {
        const scoped_lock lock(m_mutex);

        m_turned_away = m_turned_away || turned_away;

        if (m_closed) {
            return;
        }

        m_closed = true;
        m_failure = failure;
    }

    m_sending.notify_all();
}

// Fails every request still waiting, once the reader has stopped: the
// oldest with what closed the connection, and the others with Closed,
// saying so.
void PeerConnection::_fail_waiting() {
    std::deque<promise<Response>> waiting;
    exception_ptr failure;

    {
        const scoped_lock lock(m_mutex);

        waiting.swap(m_waiting);
        m_unsent.clear();
        failure = m_failure;
    }

    if (waiting.empty()) {
        return;
    }

    const auto because = "The connection to " + m_address.to_string()
                         + " closed before the answer came: " + reason(failure);

    waiting.front().set_exception(failure);
    waiting.pop_front();

    for (auto& request : waiting) {
        request.set_exception(make_exception_ptr(Closed(because)));
    }
}

} // namespace peer
