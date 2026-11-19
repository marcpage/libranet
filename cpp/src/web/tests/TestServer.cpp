#include <gtest/gtest.h>
#include <chrono>
#include <cstddef>
#include <future>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "web/Server.hpp"

using http::Request;
using http::Response;
using net::Address;
using net::ISocket;
using web::Server;

namespace {

constexpr std::string_view ok_request = "GET /ok HTTP/1.1\r\n\r\n";
constexpr std::string_view stop_request = "GET /stop HTTP/1.1\r\n\r\n";
constexpr std::string_view ok_status = "HTTP/1.1 200 OK";

// A port that was free a moment ago, from a socket bound to port 0.
Address free_address() {
    const auto socket = ISocket::bind(Address("127.0.0.1", 0));

    if (!socket) {
        throw std::runtime_error("bind() returned no socket");
    }

    return socket->local_address();
}

// A connection to the server. start() runs on another thread, so the first
// tries can come before the server listens.
ISocket::Ptr connect(const Address& address) {
    constexpr auto attempts = 100;
    constexpr auto retry_delay = std::chrono::milliseconds(50);
    constexpr auto timeout = std::chrono::seconds(5);

    for (auto attempt = 1;; ++attempt) {
        try {
            auto client = ISocket::connect(address);

            client->set_timeout(timeout);
            return client;
        } catch (const std::system_error&) {
            if (attempt == attempts) {
                throw;
            }

            std::this_thread::sleep_for(retry_delay);
        }
    }
}

// The status line the server answers the request with.
std::string status_of(const Address& address, std::string_view request) {
    const auto client = connect(address);

    client->send_all(request);
    return client->read_line();
}

// Handlers for /ok, which answers 200 OK, and /stop, which stops the server.
void add_test_handlers(Server& server) {
    server.register_handler("/ok", []([[maybe_unused]] const Request& request,
                                      [[maybe_unused]] Response& response) {});
    server.register_handler("/stop", [&server]([[maybe_unused]] const Request& request,
                                               [[maybe_unused]] Response& response) {
        server.stop();
    });
}

// A handler that throws, as storage's does for a request for /data/x, whose
// identifier has no hash.
void throwing_handler([[maybe_unused]] const Request& request,
                      [[maybe_unused]] Response& response) {
    throw std::invalid_argument("Invalid identifier format: missing hash component.");
}

// Runs the server on a thread of its own. The future holds what start()
// throws, once it returns.
std::future<void> run(Server& server) {
    return std::async(std::launch::async, [&server] { server.start(1); });
}

} // namespace

TEST(ServerTest, HandlerThatThrowsIsAnsweredWithInternalServerError) {
    const auto address = free_address();
    Server server(address);

    server.register_handler("/data", throwing_handler);
    add_test_handlers(server);

    auto running = run(server);

    EXPECT_EQ(status_of(address, "GET /data/x HTTP/1.1\r\n\r\n"),
              "HTTP/1.1 500 Internal Server Error");
    EXPECT_EQ(status_of(address, ok_request), ok_status);
    EXPECT_EQ(status_of(address, stop_request), ok_status);
    EXPECT_NO_THROW(running.get());
}

TEST(ServerTest, HeaderWithoutColonIsAnsweredWithBadRequest) {
    const auto address = free_address();
    Server server(address);

    add_test_handlers(server);

    auto running = run(server);

    EXPECT_EQ(status_of(address, "GET /ok HTTP/1.1\r\nNo colon here\r\n"),
              "HTTP/1.1 400 Bad Request");
    EXPECT_EQ(status_of(address, ok_request), ok_status);
    EXPECT_EQ(status_of(address, stop_request), ok_status);
    EXPECT_NO_THROW(running.get());
}

TEST(ServerTest, ClientThatLeavesBeforeItsReplyDoesNotStopTheServer) {
    // Larger than a socket's send buffer, so sending it fails once the
    // client is gone.
    constexpr std::size_t large_body_bytes = 16UL * 1024UL * 1024UL;
    const auto address = free_address();
    Server server(address);
    std::promise<void> client_left;
    const auto left = client_left.get_future();

    server.register_handler("/large", [&left]([[maybe_unused]] const Request& request,
                                              Response& response) {
        left.wait();
        response.set_body(std::string(large_body_bytes, 'x'));
    });
    add_test_handlers(server);

    auto running = run(server);
    auto client = connect(address);

    client->send_all("GET /large HTTP/1.1\r\n\r\n");
    client->close();
    client_left.set_value();

    EXPECT_EQ(status_of(address, ok_request), ok_status);
    EXPECT_EQ(status_of(address, stop_request), ok_status);
    EXPECT_NO_THROW(running.get());
}
