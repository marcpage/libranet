#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <cstddef>
#include <future>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <utility>
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

// The connections the server queues while it answers one.
constexpr auto backlog = 8;

// How long to wait, at most, for the server to listen, and how long between
// tries. The server binds when it is made, but listens only once its thread
// has started it. Until then, Linux refuses a connection to it, and macOS
// holds one until it listens.
constexpr auto listen_timeout = std::chrono::seconds(30);
constexpr auto retry_interval = std::chrono::milliseconds(5);

// How many bytes to read from a connection at a time.
constexpr std::size_t read_size = 4096;

// What the server answers, as problem details (HTTP API §17), to a request
// it cannot read, for a path with no handler, and when a handler throws.
constexpr std::string_view bad_request =
    "HTTP/1.1 400 Bad Request\r\nContent-Type: application/problem+json\r\n\r\n"
    R"({"detail":"The request could not be read.","status":400,)"
    R"("title":"Bad Request","type":"about:blank"})";
constexpr std::string_view not_found =
    "HTTP/1.1 404 Not Found\r\nContent-Type: application/problem+json\r\n\r\n"
    R"({"detail":"No resource exists at this path.","status":404,)"
    R"("title":"Not Found","type":"about:blank"})";
constexpr std::string_view internal_server_error =
    "HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/problem+json\r\n\r\n"
    R"({"detail":"The request could not be answered.","status":500,)"
    R"("title":"Internal Server Error","type":"about:blank"})";

// A connection to address, once the server there is listening.
ISocket::Ptr connect_when_listening(const Address& address) {
    const auto deadline = std::chrono::steady_clock::now() + listen_timeout;

    while (true) {
        try {
            return ISocket::connect(address);
        } catch (const std::system_error&) {
            if (std::chrono::steady_clock::now() >= deadline) {
                throw;
            }
        }

        std::this_thread::sleep_for(retry_interval);
    }
}

// All the server sends on connection, until it closes it.
std::string read_all(ISocket& connection) {
    std::string text;
    std::vector<std::byte> buffer(read_size);

    while (connection.receive(buffer)) {
        std::transform(buffer.begin(), buffer.end(), std::back_inserter(text), [](std::byte byte) {
            return static_cast<char>(std::to_integer<unsigned char>(byte));
        });
        buffer.resize(read_size);
    }

    return text;
}

// What the server at address answers to request, sent on a connection of its
// own. The server reads all of each request sent here, so that it closes the
// connection with nothing left unread, which would reset it.
std::string exchange(const Address& address, std::string_view request) {
    const auto connection = connect_when_listening(address);

    connection->send_all(request);
    return read_all(*connection);
}

// A handler that answers with body.
Server::RequestHandler answer_with(std::string body) {
    return [body = std::move(body)](const Request& /*request*/, Response& response) {
        response.set_body(body);
    };
}

// A handler that throws, as storage's does for a signed request for /data/x,
// whose identifier has no hash.
void throw_invalid_identifier(const Request& /*request*/, Response& /*response*/) {
    throw std::invalid_argument("Invalid identifier format: missing hash component.");
}

// A server on the loopback address, at a port the system picks, which a test
// starts on a thread of its own once it has registered its handlers. It is
// stopped by a request to a handler of its own, so that stop() is called on
// the server's thread, as any handler's work is.
class ServerTest : public testing::Test {
protected:
    ServerTest();

    void TearDown() override;

    [[nodiscard]] Server& server() noexcept;
    [[nodiscard]] const Address& address() const noexcept;

    void start();
    void stop();
    // Waits for start() to return, once stop() has been called.
    void join();

private:
    Server m_server;
    Address m_address;
    std::thread m_thread;
};

ServerTest::ServerTest()
    : m_server(Address("127.0.0.1", 0))
    , m_address(m_server.local_address()) {
    m_server.register_handler("/stop", [this](const Request& /*request*/, Response& /*response*/) {
        m_server.stop();
    });
}

void ServerTest::TearDown() {
    stop();
}

Server& ServerTest::server() noexcept {
    return m_server;
}

const Address& ServerTest::address() const noexcept {
    return m_address;
}

void ServerTest::start() {
    m_thread = std::thread([this] {
        m_server.start(backlog);
    });
}

void ServerTest::stop() {
    if (!m_thread.joinable()) {
        return;
    }

    (void)exchange(m_address, "GET /stop HTTP/1.1\r\n\r\n");
    m_thread.join();
}

void ServerTest::join() {
    m_thread.join();
}

} // namespace

TEST_F(ServerTest, LocalAddressHasThePortBound) {
    EXPECT_EQ(address().host(), "127.0.0.1");
    EXPECT_NE(address().port(), 0);
}

TEST_F(ServerTest, HandlerForThePathAnswers) {
    server().register_handler("/hello", answer_with("Hello"));
    start();

    EXPECT_EQ(exchange(address(), "GET /hello HTTP/1.1\r\n\r\n"), "HTTP/1.1 200 OK\r\n\r\nHello");
}

// A path no handler is registered for goes to the handler for the longest
// path it is under: one it starts with, whole segments at a time.
TEST_F(ServerTest, LongestPathAtTheStartChoosesTheHandler) {
    server().register_handler("/a", answer_with("a"));
    server().register_handler("/a/b", answer_with("a/b"));
    start();

    EXPECT_EQ(exchange(address(), "GET /a HTTP/1.1\r\n\r\n"), "HTTP/1.1 200 OK\r\n\r\na");
    EXPECT_EQ(exchange(address(), "GET /a/b/c HTTP/1.1\r\n\r\n"), "HTTP/1.1 200 OK\r\n\r\na/b");
    EXPECT_EQ(exchange(address(), "GET /a/c HTTP/1.1\r\n\r\n"), "HTTP/1.1 200 OK\r\n\r\na");
    EXPECT_EQ(exchange(address(), "GET /a/bc HTTP/1.1\r\n\r\n"), "HTTP/1.1 200 OK\r\n\r\na");
}

TEST_F(ServerTest, PathWithNoHandlerIsNotFound) {
    start();

    EXPECT_EQ(exchange(address(), "GET /missing HTTP/1.1\r\n\r\n"), not_found);
}

// A URI that only starts with the letters of a path, as /database does with
// /data, is not under it.
TEST_F(ServerTest, PathIsNotAPrefixOfALongerName) {
    server().register_handler("/data", answer_with("data"));
    start();

    EXPECT_EQ(exchange(address(), "GET /database HTTP/1.1\r\n\r\n"), not_found);
    EXPECT_EQ(exchange(address(), "GET /data-set HTTP/1.1\r\n\r\n"), not_found);
    EXPECT_EQ(exchange(address(), "GET /dat HTTP/1.1\r\n\r\n"), not_found);
}

TEST_F(ServerTest, PathHandlesWhatIsBelowIt) {
    server().register_handler("/data", answer_with("data"));
    start();

    EXPECT_EQ(exchange(address(), "GET /data/ HTTP/1.1\r\n\r\n"), "HTTP/1.1 200 OK\r\n\r\ndata");
    EXPECT_EQ(exchange(address(), "GET /data/abc HTTP/1.1\r\n\r\n"), "HTTP/1.1 200 OK\r\n\r\ndata");
    EXPECT_EQ(exchange(address(), "GET /data?key=value HTTP/1.1\r\n\r\n"),
              "HTTP/1.1 200 OK\r\n\r\ndata");
}

// A path that ends in a slash has every URI that starts with it.
TEST_F(ServerTest, PathEndingInSlashHandlesEverythingStartingWithIt) {
    server().register_handler("/", answer_with("root"));
    server().register_handler("/files/", answer_with("files"));
    start();

    EXPECT_EQ(exchange(address(), "GET /database HTTP/1.1\r\n\r\n"), "HTTP/1.1 200 OK\r\n\r\nroot");
    EXPECT_EQ(exchange(address(), "GET /files/abc HTTP/1.1\r\n\r\n"), "HTTP/1.1 200 OK\r\n\r\nfiles");
    EXPECT_EQ(exchange(address(), "GET /files HTTP/1.1\r\n\r\n"), "HTTP/1.1 200 OK\r\n\r\nroot");
    EXPECT_EQ(exchange(address(), "GET /filesystem HTTP/1.1\r\n\r\n"), "HTTP/1.1 200 OK\r\n\r\nroot");
}

TEST_F(ServerTest, HandlerReadsTheHeadersAndBody) {
    server().register_handler("/echo", [](const Request& request, Response& response) {
        response.headers().set("X-Test", std::string(request.headers().get("X-Test", "")));
        response.set_body(std::string(request.method()) + " " + std::string(request.body()));
    });
    start();

    EXPECT_EQ(exchange(address(),
                       "POST /echo HTTP/1.1\r\nX-Test: value\r\nContent-Length: 5\r\n\r\nhello"),
              "HTTP/1.1 200 OK\r\nX-Test: value\r\n\r\nPOST hello");
}

TEST_F(ServerTest, EmptyRequestLineIsBadRequest) {
    start();

    EXPECT_EQ(exchange(address(), "\r\n"), bad_request);
}

TEST_F(ServerTest, ContentLengthNotANumberIsBadRequest) {
    start();

    EXPECT_EQ(exchange(address(), "POST /x HTTP/1.1\r\nContent-Length: five\r\n\r\n"), bad_request);
}

TEST_F(ServerTest, ContentLengthOutOfRangeIsBadRequest) {
    start();

    EXPECT_EQ(exchange(address(),
                       "POST /x HTTP/1.1\r\nContent-Length: 99999999999999999999999\r\n\r\n"),
              bad_request);
}

// Once stopped, the server no longer listens.
TEST_F(ServerTest, StopEndsStart) {
    start();

    stop();

    EXPECT_THROW((void)ISocket::connect(address()), std::system_error);
}

// A server stopped before it starts never listens.
TEST_F(ServerTest, StartAfterStopReturnsAtOnce) {
    server().stop();

    server().start(backlog);

    EXPECT_THROW((void)ISocket::connect(address()), std::system_error);
}

// stop() ends start() from another thread, while it waits for a client.
TEST_F(ServerTest, StopFromAnotherThread) {
    server().register_handler("/hello", answer_with("Hello"));
    start();
    ASSERT_EQ(exchange(address(), "GET /hello HTTP/1.1\r\n\r\n"), "HTTP/1.1 200 OK\r\n\r\nHello");

    server().stop();
    join();

    EXPECT_THROW((void)ISocket::connect(address()), std::system_error);
}

// A client that connects and sends nothing does not keep the server from
// stopping: its connection is ended.
TEST_F(ServerTest, StopWithAClientSendingNothing) {
    // Time for the server to take the client and wait for its request. The
    // test passes without it, but then may not stop the server mid-request.
    constexpr auto reading = std::chrono::milliseconds(100);

    server().register_handler("/hello", answer_with("Hello"));
    start();
    ASSERT_EQ(exchange(address(), "GET /hello HTTP/1.1\r\n\r\n"), "HTTP/1.1 200 OK\r\n\r\nHello");

    const auto client = ISocket::connect(address());

    client->send_all("GET /hello HTTP/1.1\r\n");
    std::this_thread::sleep_for(reading);
    server().stop();
    join();

    EXPECT_THROW((void)ISocket::connect(address()), std::system_error);
}

// A handler that throws is answered with an Internal Server Error, and the
// server goes on to answer the next request.
TEST_F(ServerTest, HandlerThatThrowsIsInternalServerError) {
    server().register_handler("/data", throw_invalid_identifier);
    server().register_handler("/hello", answer_with("Hello"));
    start();

    EXPECT_EQ(exchange(address(), "GET /data/x HTTP/1.1\r\n\r\n"), internal_server_error);
    EXPECT_EQ(exchange(address(), "GET /hello HTTP/1.1\r\n\r\n"), "HTTP/1.1 200 OK\r\n\r\nHello");
}

// The server answers as soon as it reads the header line, so the request
// ends there, leaving nothing unread.
TEST_F(ServerTest, HeaderWithoutColonIsBadRequest) {
    server().register_handler("/hello", answer_with("Hello"));
    start();

    EXPECT_EQ(exchange(address(), "GET /hello HTTP/1.1\r\nNo colon here\r\n"), bad_request);
    EXPECT_EQ(exchange(address(), "GET /hello HTTP/1.1\r\n\r\n"), "HTTP/1.1 200 OK\r\n\r\nHello");
}

// A client that leaves before its answer is sent ends only its own
// connection, and the server goes on to answer the next request.
TEST_F(ServerTest, ClientThatLeavesBeforeItsAnswerDoesNotStopTheServer) {
    // More than a connection holds unsent, so sending it fails once the
    // client is gone.
    constexpr std::size_t large_body_bytes = 16UL * 1024UL * 1024UL;
    std::promise<void> client_left;
    const auto left = client_left.get_future().share();

    server().register_handler("/large", [left](const Request& /*request*/, Response& response) {
        left.wait();
        response.set_body(std::string(large_body_bytes, 'x'));
    });
    server().register_handler("/hello", answer_with("Hello"));
    start();

    const auto connection = connect_when_listening(address());

    connection->send_all("GET /large HTTP/1.1\r\n\r\n");
    connection->close();
    client_left.set_value();

    EXPECT_EQ(exchange(address(), "GET /hello HTTP/1.1\r\n\r\n"), "HTTP/1.1 200 OK\r\n\r\nHello");
}
