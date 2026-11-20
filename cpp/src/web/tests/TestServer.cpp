#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <cstddef>
#include <future>
#include <iterator>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "web/Server.hpp"

using http::Request;
using http::Response;
using http::ResponseCode;
using net::Address;
using net::ISocket;
using web::Server;

namespace {

// The connections the system queues until the server takes them.
constexpr auto backlog = 8;

// How long to wait, at most, for the server to listen, and how long between
// tries. The server binds when it is made, but listens only once its thread
// has started it. Until then, Linux refuses a connection to it, and macOS
// holds one until it listens.
constexpr auto listen_timeout = std::chrono::seconds(30);
constexpr auto retry_interval = std::chrono::milliseconds(5);

// How long to wait, at most, for the server to send what a test reads, so
// that a test fails rather than waits forever when it sends nothing.
constexpr auto answer_timeout = std::chrono::seconds(10);

// How many bytes to read from a connection at a time.
constexpr std::size_t read_size = 4096;

// A connection to address, once the server there is listening.
ISocket::Ptr connect_when_listening(const Address& address) {
    const auto deadline = std::chrono::steady_clock::now() + listen_timeout;

    while (true) {
        try {
            auto connection = ISocket::connect(address);

            connection->set_receive_timeout(answer_timeout);
            return connection;
        } catch (const std::system_error&) {
            if (std::chrono::steady_clock::now() >= deadline) {
                throw;
            }
        }

        std::this_thread::sleep_for(retry_interval);
    }
}

void append(std::string& text, const std::vector<std::byte>& bytes) {
    std::transform(bytes.begin(), bytes.end(), std::back_inserter(text), [](std::byte byte) {
        return static_cast<char>(std::to_integer<unsigned char>(byte));
    });
}

// All the server sends on connection, until it closes it.
std::string read_all(ISocket& connection) {
    std::string text;
    std::vector<std::byte> buffer(read_size);

    while (connection.receive(buffer)) {
        append(text, buffer);
        buffer.resize(read_size);
    }

    return text;
}

// The status line and headers of the next answer the server sends on
// connection, or what it sends of them, if it closes the connection first.
std::string read_head(ISocket& connection) {
    std::string text;
    std::string line;

    while (connection.read_line(line)) {
        text += line + "\r\n";

        if (line.empty()) {
            break;
        }
    }

    return text;
}

// The next answer the server sends on connection: its head, and as many
// bytes of body as its Content-Length gives.
std::string read_answer(ISocket& connection) {
    constexpr std::string_view length_header = "\r\nContent-Length: ";
    auto text = read_head(connection);
    const auto found = text.find(length_header);

    if (std::string::npos != found) {
        append(text, connection.read_exact(std::stoul(text.substr(found + length_header.size()))));
    }

    return text;
}

// What the server at address answers to request, sent on a connection of its
// own, which is closed once the answer is read.
std::string ask(const Address& address, std::string_view request) {
    const auto connection = connect_when_listening(address);

    connection->send_all(request);
    return read_answer(*connection);
}

// All the server at address sends after request, sent on a connection of its
// own, until it closes the connection. The server reads all of each request
// sent here, so that it closes the connection with nothing left unread,
// which would reset it.
std::string ask_until_closed(const Address& address, std::string_view request) {
    const auto connection = connect_when_listening(address);

    connection->send_all(request);
    return read_all(*connection);
}

// A request for target, with no headers.
std::string get(std::string_view target) {
    return "GET " + std::string(target) + " HTTP/1.1\r\n\r\n";
}

// The server's answer, as status says, with body, to a request for target on
// a connection it keeps open.
std::string answer(std::string_view status, std::string_view target, std::string_view body) {
    return "HTTP/1.1 " + std::string(status) + "\r\nContent-Length: "
           + std::to_string(body.size()) + "\r\nX-Request-Path: " + std::string(target)
           + "\r\n\r\n" + std::string(body);
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

// A handler that stops server.
Server::RequestHandler stopping(Server& server) {
    return [&server](const Request& /*request*/, Response& /*response*/) {
        server.stop();
    };
}

// A server on the loopback address, at a port the system picks, which a test
// starts on a thread of its own once it has registered its handlers.
class ServerTest : public testing::Test {
protected:
    ServerTest();

    void TearDown() override;

    // Makes the server again, with limits, before its handlers are
    // registered.
    void limit(const Server::Limits& limits);

    [[nodiscard]] Server& server() noexcept;
    [[nodiscard]] const Address& address() const noexcept;

    void start();
    void stop();
    // Waits for start() to return, once stop() has been called.
    void join();

private:
    std::unique_ptr<Server> m_server;
    Address m_address;
    std::thread m_thread;
};

ServerTest::ServerTest()
    : m_server(std::make_unique<Server>(Address("127.0.0.1", 0)))
    , m_address(m_server->local_address()) {}

void ServerTest::TearDown() {
    stop();
}

void ServerTest::limit(const Server::Limits& limits) {
    m_server = std::make_unique<Server>(Address("127.0.0.1", 0), limits);
    m_address = m_server->local_address();
}

Server& ServerTest::server() noexcept {
    return *m_server;
}

const Address& ServerTest::address() const noexcept {
    return m_address;
}

void ServerTest::start() {
    m_thread = std::thread([this] {
        m_server->start(backlog);
    });
}

void ServerTest::stop() {
    if (!m_thread.joinable()) {
        return;
    }

    m_server->stop();
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

TEST_F(ServerTest, LimitsAllowingNoConnectionAreRefused) {
    Server::Limits limits;

    limits.connections = 0;

    EXPECT_THROW(const Server refused(Address("127.0.0.1", 0), limits), std::invalid_argument);
}

TEST_F(ServerTest, HandlerForThePathAnswers) {
    server().register_handler("/hello", answer_with("Hello"));
    start();

    EXPECT_EQ(ask(address(), get("/hello")),
              "HTTP/1.1 200 OK\r\nContent-Length: 5\r\nX-Request-Path: /hello\r\n\r\nHello");
}

// A path no handler is registered for goes to the handler for the longest
// path it is under: one it starts with, whole segments at a time.
TEST_F(ServerTest, LongestPathAtTheStartChoosesTheHandler) {
    server().register_handler("/a", answer_with("a"));
    server().register_handler("/a/b", answer_with("a/b"));
    start();

    EXPECT_EQ(ask(address(), get("/a")), answer("200 OK", "/a", "a"));
    EXPECT_EQ(ask(address(), get("/a/b/c")), answer("200 OK", "/a/b/c", "a/b"));
    EXPECT_EQ(ask(address(), get("/a/c")), answer("200 OK", "/a/c", "a"));
    EXPECT_EQ(ask(address(), get("/a/bc")), answer("200 OK", "/a/bc", "a"));
}

TEST_F(ServerTest, PathWithNoHandlerIsNotFound) {
    start();

    EXPECT_EQ(ask(address(), get("/missing")),
              "HTTP/1.1 404 Not Found\r\nContent-Length: 11\r\nX-Request-Path: /missing\r\n\r\n"
              "Not Found\r\n");
}

// A URI that only starts with the letters of a path, as /database does with
// /data, is not under it.
TEST_F(ServerTest, PathIsNotAPrefixOfALongerName) {
    server().register_handler("/data", answer_with("data"));
    start();

    EXPECT_EQ(ask(address(), get("/database")),
              answer("404 Not Found", "/database", "Not Found\r\n"));
    EXPECT_EQ(ask(address(), get("/data-set")),
              answer("404 Not Found", "/data-set", "Not Found\r\n"));
    EXPECT_EQ(ask(address(), get("/dat")), answer("404 Not Found", "/dat", "Not Found\r\n"));
}

TEST_F(ServerTest, PathHandlesWhatIsBelowIt) {
    server().register_handler("/data", answer_with("data"));
    start();

    EXPECT_EQ(ask(address(), get("/data/")), answer("200 OK", "/data/", "data"));
    EXPECT_EQ(ask(address(), get("/data/abc")), answer("200 OK", "/data/abc", "data"));
    EXPECT_EQ(ask(address(), get("/data?key=value")),
              answer("200 OK", "/data?key=value", "data"));
}

// A path that ends in a slash has every URI that starts with it.
TEST_F(ServerTest, PathEndingInSlashHandlesEverythingStartingWithIt) {
    server().register_handler("/", answer_with("root"));
    server().register_handler("/files/", answer_with("files"));
    start();

    EXPECT_EQ(ask(address(), get("/database")), answer("200 OK", "/database", "root"));
    EXPECT_EQ(ask(address(), get("/files/abc")), answer("200 OK", "/files/abc", "files"));
    EXPECT_EQ(ask(address(), get("/files")), answer("200 OK", "/files", "root"));
    EXPECT_EQ(ask(address(), get("/filesystem")), answer("200 OK", "/filesystem", "root"));
}

TEST_F(ServerTest, HandlerReadsTheHeadersAndBody) {
    server().register_handler("/echo", [](const Request& request, Response& response) {
        response.headers().set("X-Test", std::string(request.headers().get("X-Test", "")));
        response.set_body(std::string(request.method()) + " " + std::string(request.body()));
    });
    start();

    EXPECT_EQ(ask(address(),
                  "POST /echo HTTP/1.1\r\nX-Test: value\r\nContent-Length: 5\r\n\r\nhello"),
              "HTTP/1.1 200 OK\r\nContent-Length: 10\r\nX-Request-Path: /echo\r\nX-Test: value\r\n"
              "\r\nPOST hello");
}

// One connection carries one request after another.
TEST_F(ServerTest, ConnectionStaysOpenForTheNextRequest) {
    server().register_handler("/a", answer_with("a"));
    server().register_handler("/b", answer_with("b"));
    start();

    const auto connection = connect_when_listening(address());

    connection->send_all(get("/a"));
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/a", "a"));
    connection->send_all(get("/b"));
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/b", "b"));
}

// Requests sent without waiting for their answers are answered in the order
// they came, the slower first.
TEST_F(ServerTest, RequestsSentWithoutWaitingAreAnsweredInOrder) {
    constexpr auto slow = std::chrono::milliseconds(50);

    server().register_handler("/slow", [slow](const Request& /*request*/, Response& response) {
        std::this_thread::sleep_for(slow);
        response.set_body("slow");
    });
    server().register_handler("/fast", answer_with("fast"));
    start();

    const auto connection = connect_when_listening(address());

    connection->send_all(get("/slow") + get("/fast") + get("/slow?again"));
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/slow", "slow"));
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/fast", "fast"));
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/slow?again", "slow"));
}

// A request saying Connection: close, alone or in a list, in any case, is
// answered so, and its connection then closed.
TEST_F(ServerTest, RequestSayingCloseClosesTheConnection) {
    server().register_handler("/hello", answer_with("Hello"));
    start();

    EXPECT_EQ(ask_until_closed(address(), "GET /hello HTTP/1.1\r\nConnection: close\r\n\r\n"),
              "HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 5\r\n"
              "X-Request-Path: /hello\r\n\r\nHello");
    EXPECT_EQ(ask_until_closed(address(),
                               "GET /hello HTTP/1.1\r\nConnection: Upgrade, Close\r\n\r\n"),
              "HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 5\r\n"
              "X-Request-Path: /hello\r\n\r\nHello");
}

TEST_F(ServerTest, HandlerSayingCloseClosesTheConnection) {
    server().register_handler("/bye", [](const Request& /*request*/, Response& response) {
        response.headers().set("Connection", "close");
        response.set_body("Bye");
    });
    start();

    EXPECT_EQ(ask_until_closed(address(), get("/bye")),
              "HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 3\r\n"
              "X-Request-Path: /bye\r\n\r\nBye");
}

// HTTP/1.0 closes a connection after each answer by default.
TEST_F(ServerTest, Http10RequestClosesTheConnection) {
    server().register_handler("/hello", answer_with("Hello"));
    start();

    EXPECT_EQ(ask_until_closed(address(), "GET /hello HTTP/1.0\r\n\r\n"),
              "HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 5\r\n"
              "X-Request-Path: /hello\r\n\r\nHello");
}

// The answer to a HEAD gives the length of the body a GET would have, and
// sends none of it, so the next answer on the connection is read right.
TEST_F(ServerTest, HeadIsAnsweredWithTheLengthAlone) {
    server().register_handler("/hello", answer_with("Hello"));
    start();

    const auto connection = connect_when_listening(address());

    connection->send_all("HEAD /hello HTTP/1.1\r\n\r\n" + get("/hello"));
    EXPECT_EQ(read_head(*connection),
              "HTTP/1.1 200 OK\r\nContent-Length: 5\r\nX-Request-Path: /hello\r\n\r\n");
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/hello", "Hello"));
}

// An answer with a status that has no body sends none, even if its handler
// gave one.
TEST_F(ServerTest, NoContentIsAnsweredWithoutABody) {
    server().register_handler("/empty", [](const Request& /*request*/, Response& response) {
        response.set_status_code(ResponseCode::Status::NoContent);
        response.set_body("ignored");
    });
    server().register_handler("/hello", answer_with("Hello"));
    start();

    const auto connection = connect_when_listening(address());

    connection->send_all(get("/empty") + get("/hello"));
    EXPECT_EQ(read_answer(*connection),
              "HTTP/1.1 204 No Content\r\nX-Request-Path: /empty\r\n\r\n");
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/hello", "Hello"));
}

// A request line the server cannot read names no request, and leaves where
// the next request starts in doubt, so the connection is closed.
TEST_F(ServerTest, EmptyRequestLineIsBadRequest) {
    start();

    EXPECT_EQ(ask_until_closed(address(), "\r\n"),
              "HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 13\r\n\r\n"
              "Bad Request\r\n");
}

// A Content-Length is digits alone.
TEST_F(ServerTest, ContentLengthNotDigitsIsBadRequest) {
    start();

    for (const auto* const length : {"five", "-1", "+5", "5 5", ""}) {
        EXPECT_EQ(ask_until_closed(address(), std::string("POST /x HTTP/1.1\r\nContent-Length: ")
                                                  + length + "\r\n\r\n"),
                  "HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 13\r\n"
                  "X-Request-Path: /x\r\n\r\nBad Request\r\n")
            << length;
    }
}

TEST_F(ServerTest, ContentLengthOutOfRangeIsPayloadTooLarge) {
    start();

    EXPECT_EQ(ask_until_closed(
                  address(), "POST /x HTTP/1.1\r\nContent-Length: 99999999999999999999999\r\n\r\n"),
              "HTTP/1.1 413 Payload Too Large\r\nConnection: close\r\nContent-Length: 19\r\n"
              "X-Request-Path: /x\r\n\r\nPayload Too Large\r\n");
}

// A body is taken up to the limit, and one past it is refused unread.
TEST_F(ServerTest, BodyPastTheLimitIsPayloadTooLarge) {
    constexpr std::size_t most_bytes = 4;
    Server::Limits limits;

    limits.body_bytes = most_bytes;
    limit(limits);
    server().register_handler("/echo", [](const Request& request, Response& response) {
        response.set_body(std::string(request.body()));
    });
    start();

    EXPECT_EQ(ask(address(), "POST /echo HTTP/1.1\r\nContent-Length: 4\r\n\r\nabcd"),
              answer("200 OK", "/echo", "abcd"));
    EXPECT_EQ(ask_until_closed(address(), "POST /echo HTTP/1.1\r\nContent-Length: 5\r\n\r\n"),
              "HTTP/1.1 413 Payload Too Large\r\nConnection: close\r\nContent-Length: 19\r\n"
              "X-Request-Path: /echo\r\n\r\nPayload Too Large\r\n");
}

// A body sent in chunks has no length to say where the next request starts.
TEST_F(ServerTest, ChunkedBodyIsNotImplemented) {
    start();

    EXPECT_EQ(ask_until_closed(address(),
                               "POST /x HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"),
              "HTTP/1.1 501 Not Implemented\r\nConnection: close\r\nContent-Length: 17\r\n"
              "X-Request-Path: /x\r\n\r\nNot Implemented\r\n");
}

// The request line must fit in the limit on a request's line and headers.
// The server reads only as far as the limit, which is all that is sent.
TEST_F(ServerTest, RequestLinePastTheLimitIsUriTooLong) {
    constexpr std::size_t head_bytes = 32;
    Server::Limits limits;

    limits.head_bytes = head_bytes;
    limit(limits);
    start();

    EXPECT_EQ(ask_until_closed(address(), "GET /" + std::string(head_bytes - 5, 'a')),
              "HTTP/1.1 414 URI Too Long\r\nConnection: close\r\nContent-Length: 14\r\n\r\n"
              "URI Too Long\r\n");
}

// A request's line and headers are taken up to the limit, the blank line
// that ends them included, and refused past it.
TEST_F(ServerTest, HeadersPastTheLimitAreTooLarge) {
    constexpr std::size_t head_bytes = 32;
    Server::Limits limits;

    limits.head_bytes = head_bytes;
    limit(limits);
    server().register_handler("/hello", answer_with("Hello"));
    start();

    // 21 bytes of request line, 9 of header, and 2 of blank line.
    EXPECT_EQ(ask(address(), "GET /hello HTTP/1.1\r\nX-A: 12\r\n\r\n"),
              answer("200 OK", "/hello", "Hello"));
    EXPECT_EQ(ask_until_closed(address(), "GET /hello HTTP/1.1\r\nX-A: 123\r\n\r"),
              "HTTP/1.1 431 Request Header Fields Too Large\r\nConnection: close\r\n"
              "Content-Length: 33\r\nX-Request-Path: /hello\r\n\r\n"
              "Request Header Fields Too Large\r\n");
}

// A connection that sends nothing for the idle limit is closed.
TEST_F(ServerTest, IdleConnectionIsClosed) {
    constexpr auto idle = std::chrono::milliseconds(100);
    Server::Limits limits;

    limits.idle = idle;
    limit(limits);
    server().register_handler("/hello", answer_with("Hello"));
    start();

    const auto connection = connect_when_listening(address());

    connection->send_all(get("/hello"));
    ASSERT_EQ(read_answer(*connection), answer("200 OK", "/hello", "Hello"));
    EXPECT_EQ(read_all(*connection), "");
}

// A client holding a connection open, halfway through a request, does not
// keep another from being answered.
TEST_F(ServerTest, ClientsAreServedAtOnce) {
    server().register_handler("/hello", answer_with("Hello"));
    start();

    const auto holding = connect_when_listening(address());

    holding->send_all(get("/hello"));
    ASSERT_EQ(read_answer(*holding), answer("200 OK", "/hello", "Hello"));
    holding->send_all("GET /hello HTTP/1.1\r\n");

    EXPECT_EQ(ask(address(), get("/hello")), answer("200 OK", "/hello", "Hello"));
}

// A client past the limit on connections is served once one closes.
TEST_F(ServerTest, ConnectionsPastTheLimitWaitForOneToClose) {
    constexpr auto waiting = std::chrono::milliseconds(200);
    Server::Limits limits;

    limits.connections = 1;
    limit(limits);
    server().register_handler("/hello", answer_with("Hello"));
    start();

    auto first = connect_when_listening(address());

    first->send_all(get("/hello"));
    ASSERT_EQ(read_answer(*first), answer("200 OK", "/hello", "Hello"));

    const auto second = connect_when_listening(address());

    second->send_all(get("/hello"));
    second->set_receive_timeout(waiting);
    EXPECT_THROW((void)read_answer(*second), std::system_error);

    first.reset();
    second->set_receive_timeout(answer_timeout);
    EXPECT_EQ(read_answer(*second), answer("200 OK", "/hello", "Hello"));
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

// stop() ends start() from a handler, on the thread serving a connection.
TEST_F(ServerTest, StopFromAHandler) {
    server().register_handler("/stop", stopping(server()));
    start();

    // The connection is ended before the answer is sent.
    EXPECT_EQ(ask(address(), get("/stop")), "");
    join();

    EXPECT_THROW((void)ISocket::connect(address()), std::system_error);
}

// Clients that hold connections open, sending nothing or halfway through a
// request, do not keep the server from stopping: their connections are
// ended.
TEST_F(ServerTest, StopWithClientsHoldingConnections) {
    // Time for the server to take the clients and wait for their requests.
    // The test passes without it, but then may not stop the server
    // mid-request.
    constexpr auto reading = std::chrono::milliseconds(100);

    server().register_handler("/hello", answer_with("Hello"));
    start();

    const auto idle = connect_when_listening(address());

    idle->send_all(get("/hello"));
    ASSERT_EQ(read_answer(*idle), answer("200 OK", "/hello", "Hello"));

    const auto halfway = connect_when_listening(address());

    halfway->send_all("GET /hello HTTP/1.1\r\n");
    std::this_thread::sleep_for(reading);
    server().stop();
    join();

    EXPECT_THROW((void)ISocket::connect(address()), std::system_error);
    EXPECT_EQ(read_all(*idle), "");
    EXPECT_EQ(read_all(*halfway), "");
}

// A handler that throws is answered with an Internal Server Error, and the
// server goes on to answer the next request on the connection.
TEST_F(ServerTest, HandlerThatThrowsIsInternalServerError) {
    server().register_handler("/data", throw_invalid_identifier);
    server().register_handler("/hello", answer_with("Hello"));
    start();

    const auto connection = connect_when_listening(address());

    connection->send_all(get("/data/x"));
    EXPECT_EQ(read_answer(*connection),
              answer("500 Internal Server Error", "/data/x", "Internal Server Error\r\n"));
    connection->send_all(get("/hello"));
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/hello", "Hello"));
}

// The server answers as soon as it reads the header line, so the request
// ends there, leaving nothing unread.
TEST_F(ServerTest, HeaderWithoutColonIsBadRequest) {
    server().register_handler("/hello", answer_with("Hello"));
    start();

    EXPECT_EQ(ask_until_closed(address(), "GET /hello HTTP/1.1\r\nNo colon here\r\n"),
              "HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 13\r\n"
              "X-Request-Path: /hello\r\n\r\nBad Request\r\n");
    EXPECT_EQ(ask(address(), get("/hello")), answer("200 OK", "/hello", "Hello"));
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

    connection->send_all(get("/large"));
    connection->close();
    client_left.set_value();

    EXPECT_EQ(ask(address(), get("/hello")), answer("200 OK", "/hello", "Hello"));
}
