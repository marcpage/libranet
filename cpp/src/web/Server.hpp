#pragma once

#include <atomic>
#include <functional>
#include <string>
#include <string_view>
#include <unordered_map>

#include "../http/Request.hpp"
#include "../http/Response.hpp"
#include "../net/ISocket.hpp"

namespace web {

class Server {
public:
    using RequestHandler = std::function<void(const http::Request& req, http::Response& res)>;

    explicit Server(net::Address address);
    ~Server() = default;

    void register_handler(std::string_view path, RequestHandler handler);
    void start(int backlog);
    void stop();

    Server() = delete;
    Server(Server&) = delete;
    Server& operator=(Server&) = delete;
    Server(Server&&) = delete;
    Server& operator=(Server&&) = delete;

private:
    net::Address m_address;
    net::ISocket::Ptr m_listener;
    std::atomic<bool> m_running{false};
    std::unordered_map<std::string, RequestHandler> m_handlers;

    [[nodiscard]] RequestHandler _find_handler(std::string_view uri) const;
};

} // namespace web