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
#include <utility>
#include <vector>

#include "ConnectionInfo.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "types/Constants.hpp"

namespace web {

// An HTTP/1.1 server, with a thread for each connection. Each answer names the
// path and query it answers in X-Request-Path, and is logged at Info.
class Server {
public:
    using RequestHandler = std::function<void(const http::Request& req, http::Response& res,
                                              ConnectionInfo& connection)>;

    // Four times the 32 connections a Python node keeps to its peers; longer
    // than a peer waits between reads of the seek list (30 seconds); as many
    // bytes as the Python node takes in a line; and the largest block, 1 MiB.
    static constexpr std::size_t default_connections = 128;
    static constexpr std::chrono::milliseconds default_idle{std::chrono::seconds(60)};
    static constexpr std::size_t default_head_bytes = 64 * types::Kilobyte;
    static constexpr std::size_t default_body_bytes = 1 * types::Megabyte;

    struct Limits {
        // At least one. Those beyond it wait until one closes.
        std::size_t connections{default_connections};
        // Without sending or taking a byte; zero for no limit.
        std::chrono::milliseconds idle{default_idle};
        // A request's line and headers, line ends included: beyond it, 414 or
        // 431.
        std::size_t head_bytes{default_head_bytes};
        // A larger body is answered 413, and not read.
        std::size_t body_bytes{default_body_bytes};
    };

    explicit Server(net::Address address);
    // Throws std::invalid_argument if limits allows no connection.
    Server(net::Address address, const Limits& limits);

    // Before start(). Handlers are called from the threads serving
    // connections, several at once.
    void register_handler(std::string_view path, RequestHandler handler);
    // Before start(). Every answer carries the header, in place of any its
    // handler gave, the server's own among them.
    void set_on_every_answer(std::string name, std::string value);
    [[nodiscard]] net::Address local_address() const;
    // Serves until stop(), then ends every connection and waits for its
    // threads; at once if stop() was called before.
    void start(int backlog);
    // Any thread may call it, a handler's among them.
    void stop();

    ~Server() = default;

    Server() = delete;
    Server(Server&) = delete;
    Server& operator=(Server&) = delete;
    Server(Server&&) = delete;
    Server& operator=(Server&&) = delete;

private:
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
    std::vector<std::pair<std::string, std::string>> m_every_answer; // Header names and values

    [[nodiscard]] bool _listen(int backlog);
    [[nodiscard]] bool _wait_for_room();
    void _serve(net::ISocket::Ptr client);
    void _converse(Client& client);
    void _end_all();
};

} // namespace web
