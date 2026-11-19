#include <gtest/gtest.h>
#include <chrono>
#include <cstddef>
#include <exception>
#include <iostream>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <vector>

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

// How long a client waits for the server to listen, and to answer.
constexpr auto timeout = std::chrono::seconds(10);

// The path whose handler stops the server.
constexpr std::string_view stop_path = "/stop";

// What ends the status line and headers of an answer, before its body.
constexpr std::string_view end_of_headers = "\r\n\r\n";

// A server on the loopback address, answering on a thread of its own. Each
// of its handlers answers with the path it was registered for, and the one
// for stop_path also stops the server, which the destructor asks for.
class RunningServer {
public:
    explicit RunningServer(const std::vector<std::string>& paths);
    ~RunningServer();

    // The body of the server's answer to a GET of the URI.
    [[nodiscard]] std::string get(std::string_view uri) const;

    RunningServer(const RunningServer&) = delete;
    RunningServer& operator=(const RunningServer&) = delete;
    RunningServer(RunningServer&&) = delete;
    RunningServer& operator=(RunningServer&&) = delete;

private:
    Server m_server;
    Address m_address;
    std::thread m_thread;

    [[nodiscard]] ISocket::Ptr _connect() const;
};

RunningServer::RunningServer(const std::vector<std::string>& paths)
    : m_server(Address("127.0.0.1", 0))
    , m_address(m_server.local_address()) {
    for (const auto& path : paths) {
        m_server.register_handler(path, [path](const Request& /*unused*/, Response& response) {
            response.set_body(path);
        });
    }

    m_server.register_handler(stop_path, [this](const Request& /*unused*/, Response& response) {
        response.set_body(std::string(stop_path));
        m_server.stop();
    });

    m_thread = std::thread([this] { m_server.start(1); });
}

// A server that cannot be reached cannot be stopped, and its thread cannot be
// joined, so the tests end there.
RunningServer::~RunningServer() {
    try {
        (void)get(stop_path);
    } catch (const std::exception& error) {
        std::cerr << "Cannot stop the server: " << error.what() << '\n';
        std::terminate();
    }

    m_thread.join();
}

std::string RunningServer::get(std::string_view uri) const {
    const auto client = _connect();
    std::string answer;
    std::vector<std::byte> buffer(1);

    client->set_timeout(timeout);
    client->send_all("GET " + std::string(uri) + " HTTP/1.1\r\n\r\n");

    while (client->receive(buffer)) {
        answer.push_back(static_cast<char>(buffer.at(0)));
    }

    const auto headers_end = answer.find(end_of_headers);

    if (headers_end == std::string::npos) {
        return {};
    }

    return answer.substr(headers_end + end_of_headers.size());
}

// A connection to the server, once it listens: until it does, connecting is
// refused.
ISocket::Ptr RunningServer::_connect() const {
    const auto deadline = std::chrono::steady_clock::now() + timeout;

    while (true) {
        try {
            return ISocket::connect(m_address);
        } catch (const std::system_error&) {
            if (std::chrono::steady_clock::now() > deadline) {
                throw;
            }
        }

        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
}

} // namespace

TEST(ServerTest, PathIsNotAPrefixOfALongerName) {
    const RunningServer server({"/data"});

    EXPECT_EQ(server.get("/database"), "Not Found\r\n");
    EXPECT_EQ(server.get("/data-set"), "Not Found\r\n");
    EXPECT_EQ(server.get("/dat"), "Not Found\r\n");
}

TEST(ServerTest, PathHandlesItselfAndWhatIsBelowIt) {
    const RunningServer server({"/data"});

    EXPECT_EQ(server.get("/data"), "/data");
    EXPECT_EQ(server.get("/data/"), "/data");
    EXPECT_EQ(server.get("/data/abc"), "/data");
    EXPECT_EQ(server.get("/data?key=value"), "/data");
}

TEST(ServerTest, LongestPathWins) {
    const RunningServer server({"/data", "/data/search"});

    EXPECT_EQ(server.get("/data/search"), "/data/search");
    EXPECT_EQ(server.get("/data/search/abc"), "/data/search");
    EXPECT_EQ(server.get("/data/searching"), "/data");
    EXPECT_EQ(server.get("/data/abc"), "/data");
}

TEST(ServerTest, PathEndingInSlashHandlesEverythingStartingWithIt) {
    const RunningServer server({"/", "/files/"});

    EXPECT_EQ(server.get("/database"), "/");
    EXPECT_EQ(server.get("/files/abc"), "/files/");
    EXPECT_EQ(server.get("/files"), "/");
    EXPECT_EQ(server.get("/filesystem"), "/");
}
