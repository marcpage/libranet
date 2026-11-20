#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <functional>
#include <list>
#include <mutex>
#include <string>
#include <string_view>
#include <thread>
#include <unordered_map>

#include "ConnectionInfo.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"

namespace web {

// An HTTP/1.1 server. It serves each connection on a thread of its own, up
// to a limit, and keeps it open after each answer for the next request, so
// that requests sent without waiting for their answers are answered in the
// order they came. It closes a connection that sends nothing for a while;
// after an answer, when the request or the answer says ConnectionInfo: close, the
// request is HTTP/1.0, or the handler had the ConnectionInfo closed; and after
// answering a request it cannot read, which leaves where the next one starts
// in doubt. Each answer gives the length of its body, and names the request
// it answers, its path and query, in X-Request-Path.
class Server {
public:
    using RequestHandler = std::function<void(const http::Request& req, http::Response& res,
                                              ConnectionInfo& connection)>;

    // Each limit's default: four times the 32 connections a Python node keeps
    // to its peers; a minute, longer than a peer waits between reads of the
    // seek list (30 seconds); as many bytes as the Python node takes in a
    // line; and the largest a block can be, 1 MiB.
    static constexpr std::size_t default_connections = 128;
    static constexpr std::chrono::milliseconds default_idle{std::chrono::seconds(60)};
    static constexpr std::size_t default_head_bytes = 65536;
    static constexpr std::size_t default_body_bytes = 1048576;

    // How much a server takes on.
    struct Limits {
        // The connections served at once, at least one. Those beyond it wait
        // to be taken until one closes.
        std::size_t connections{default_connections};
        // How long a connection may go without sending or taking a byte
        // before it is closed; zero for no limit.
        std::chrono::milliseconds idle{default_idle};
        // The most bytes a request's line and headers may take, line ends
        // included. A request line that does not fit is answered 414 URI Too
        // Long, and headers that do not are answered 431 Request Header
        // Fields Too Large.
        std::size_t head_bytes{default_head_bytes};
        // The largest body a request may have. A larger one is answered 413
        // Payload Too Large, and not read.
        std::size_t body_bytes{default_body_bytes};
    };

    explicit Server(net::Address address);
    // Throws std::invalid_argument if limits allows no connection.
    Server(net::Address address, const Limits& limits);
    ~Server() = default;

    // Handlers are registered before start(), and are called from the threads
    // serving connections, several at once. Each is given the ConnectionInfo the
    // request came on, which only the thread serving it uses.
    void register_handler(std::string_view path, RequestHandler handler);
    [[nodiscard]] net::Address local_address() const;
    // Listens, and serves the clients that connect, until stop() is called;
    // then stops listening, ends every connection, waits for the threads
    // serving them, and returns. Returns at once if stop() was called before
    // it.
    void start(int backlog);
    // Makes start() return, and may be called from any thread while it runs,
    // a handler's among them. Every connection is ended, so that a client
    // sending nothing does not keep it waiting.
    void stop();

    Server() = delete;
    Server(Server&) = delete;
    Server& operator=(Server&) = delete;
    Server(Server&&) = delete;
    Server& operator=(Server&&) = delete;

private:
    // A client being served: the socket of its connection, and the thread
    // serving it.
    struct Client {
        net::ISocket::Ptr socket; // Null once the thread has closed it
        std::thread thread;
    };

    net::Address m_address;
    Limits m_limits;
    net::ISocket::Ptr m_listener;
    net::Address m_wake_address;       // Where stop() connects, to wake accept()
    std::atomic<bool> m_running{true}; // False once stop() is called
    std::mutex m_lock;                 // Orders stop() with start()
    std::condition_variable m_changed; // A connection closed, or stop() was called
    bool m_listening{false};           // Guarded by m_lock
    std::list<Client> m_connections;   // Guarded by m_lock
    std::unordered_map<std::string, RequestHandler> m_handlers;

    [[nodiscard]] RequestHandler _find_handler(std::string_view uri) const;
    [[nodiscard]] bool _listen(int backlog);
    [[nodiscard]] bool _wait_for_room();
    void _serve(net::ISocket::Ptr client);
    void _converse(Client& client);
    [[nodiscard]] bool _answer(net::ISocket& client, ConnectionInfo& connection) const;
    void _end_all();
};

} // namespace web
