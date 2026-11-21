#pragma once

#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <deque>
#include <exception>
#include <future>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "io/MeteredReader.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"

namespace peer {

// One outgoing HTTP/1.1 connection to a node, with requests sent one after
// another without waiting for each answer. One thread sends them, in the
// order they were given, and another reads the answers, each of which goes
// to the oldest request still waiting, as the node answers in the order it
// was asked (RFC 9112 §9.3.2). Anything that leaves later answers in doubt
// closes the connection and fails every request waiting: the node closing
// it, an answer that cannot be read or that nothing asked for, a failed
// send, or the node making no progress, taking a request or sending a byte,
// for the request timeout while a request waits. An answer saying
// Connection: close marks the node as having turned this node away, and
// closes the connection: it goes to its request, and those after it fail.
class PeerConnection {
public:
    using Milliseconds = std::chrono::milliseconds;

    // Why a request was not answered: the connection closed before its
    // answer came, or before it was sent.
    class Closed : public std::runtime_error {
    public:
        using std::runtime_error::runtime_error;
    };

    // The Python node's defaults for peers.connect_timeout_seconds and
    // peers.request_timeout_seconds.
    static constexpr Milliseconds default_connect_timeout{std::chrono::seconds(10)};
    static constexpr Milliseconds default_request_timeout{std::chrono::seconds(30)};

    struct Limits {
        // peers.connect_timeout_seconds: how long to wait to connect.
        Milliseconds connect_timeout{default_connect_timeout};
        // peers.request_timeout_seconds: how long the node may make no
        // progress while a request waits.
        Milliseconds request_timeout{default_request_timeout};
        // How large an answer may be.
        http::Response::Limits answer;
    };

    // Connects to the node at address. Throws std::invalid_argument, naming
    // the setting, for a timeout that is not positive; std::system_error when
    // no connection is made within limits.connect_timeout, or is refused; and
    // std::runtime_error when the address's host cannot be resolved.
    PeerConnection(net::Address address, const Limits& limits);
    // Closes the connection.
    ~PeerConnection();
    PeerConnection(const PeerConnection&) = delete;
    PeerConnection& operator=(const PeerConnection&) = delete;
    PeerConnection(PeerConnection&&) = delete;
    PeerConnection& operator=(PeerConnection&&) = delete;

    // Queues the request to be sent as HTTP/1.1, with a Host naming the node
    // and a Content-Length for its body, and gives its answer to come. The
    // answer fails with what closed the connection, for the oldest request
    // waiting, and with Closed for the others: Closed where the node closed
    // it, std::system_error where it failed or timed out, and
    // std::invalid_argument or io::LimitExceeded for an answer that could
    // not be read. Throws Closed once the connection is closed, and
    // std::invalid_argument for a request whose answer could not be told
    // from the next one's: a HEAD, whose answer gives a length it does not
    // send, one with a Transfer-Encoding, or one whose method, target, or
    // headers would not each make a line of their own.
    [[nodiscard]] std::future<http::Response> send(http::Request request);

    [[nodiscard]] const net::Address& address() const noexcept;
    // Whether requests are still taken.
    [[nodiscard]] bool is_open() const;
    // Whether the node turned this node away, answering with
    // Connection: close.
    [[nodiscard]] bool was_turned_away() const;

    // Closes the connection, failing every request still waiting, and waits
    // for its threads.
    void close();

private:
    using Clock = std::chrono::steady_clock;

    // Mistaken: the constructor sets m_address. cppcheck loses the class's
    // constructors in a file that names it with `using peer::PeerConnection;`.
    // cppcheck-suppress uninitMemberVarPrivate
    net::Address m_address;
    Limits m_limits;
    net::ISocket::Ptr m_socket;
    mutable std::mutex m_mutex;
    std::condition_variable m_sending; // A request was queued, or it closed
    std::deque<std::string> m_unsent;  // Guarded by m_mutex
    // Guarded by m_mutex: oldest first, sent or not.
    std::deque<std::promise<http::Response>> m_waiting;
    Clock::time_point m_last_progress; // Guarded by m_mutex
    bool m_closed{false};              // Guarded by m_mutex
    bool m_turned_away{false};         // Guarded by m_mutex
    std::exception_ptr m_failure;      // Guarded by m_mutex: why it closed
    std::mutex m_joining;              // Held while close() joins the threads
    std::thread m_sender;
    std::thread m_reader;

    void _send_all();
    [[nodiscard]] std::optional<std::string> _next_to_send();
    void _read_all();
    [[nodiscard]] bool _read_answer(io::MeteredReader& reader);
    [[nodiscard]] std::optional<std::size_t> _receive(std::byte* data, std::size_t size);
    [[nodiscard]] Milliseconds _time_left() const;
    void _progressed();
    [[nodiscard]] std::promise<http::Response> _take_oldest();
    void _close_for(const std::exception_ptr& failure, bool turned_away = false);
    void _end_connection() noexcept;
    void _fail_waiting();
};

} // namespace peer
