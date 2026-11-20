#pragma once

#include <atomic>
#include <functional>
#include <mutex>
#include <string>
#include <string_view>
#include <unordered_map>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "net/ISocket.hpp"

namespace web {

class Server {
public:
    using RequestHandler = std::function<void(const http::Request& req, http::Response& res)>;

    explicit Server(net::Address address);
    ~Server() = default;

    void register_handler(std::string_view path, RequestHandler handler);
    [[nodiscard]] net::Address local_address() const;
    // Listens, and answers one client at a time, until stop() is called;
    // then stops listening and returns. Returns at once if stop() was called
    // before it.
    void start(int backlog);
    // Makes start() return, and may be called from any thread while it runs.
    // The connection being answered is ended, so that a client sending
    // nothing does not keep it waiting.
    void stop();

    Server() = delete;
    Server(Server&) = delete;
    Server& operator=(Server&) = delete;
    Server(Server&&) = delete;
    Server& operator=(Server&&) = delete;

private:
    net::Address m_address;
    net::ISocket::Ptr m_listener;
    net::Address m_wake_address;       // Where stop() connects, to wake accept()
    std::atomic<bool> m_running{true}; // False once stop() is called
    std::mutex m_lock;                 // Orders stop() with start()
    bool m_listening{false};           // Guarded by m_lock
    net::ISocket* m_client{nullptr};   // Being answered; guarded by m_lock
    std::unordered_map<std::string, RequestHandler> m_handlers;

    [[nodiscard]] RequestHandler _find_handler(std::string_view uri) const;
    [[nodiscard]] bool _listen(int backlog);
    [[nodiscard]] bool _serve(net::ISocket* client);
    void _answer(net::ISocket::Ptr& client) const;
};

} // namespace web