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
#include "storage/Identifier.hpp"
#include "web/ConnectionInfo.hpp"
#include "web/Server.hpp"

using http::Request;
using http::Response;
using http::ResponseCode;
using net::Address;
using net::ISocket;
using storage::Identifier;
using web::ConnectionInfo;
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

// The problem details (HTTP API §17) the server answers with: to a request
// it cannot read, for a path with no handler, when a handler throws, to a
// request line or headers past the limit, to a body sent with a
// Transfer-Encoding, and to a body past the limit, by default and of 4
// bytes.
constexpr std::string_view bad_request =
    R"({"detail":"The request could not be read.","status":400,)"
    R"("title":"Bad Request","type":"about:blank"})";
constexpr std::string_view not_found =
    R"({"detail":"No resource exists at this path.","status":404,)"
    R"("title":"Not Found","type":"about:blank"})";
constexpr std::string_view internal_server_error =
    R"({"detail":"The request could not be answered.","status":500,)"
    R"("title":"Internal Server Error","type":"about:blank"})";
constexpr std::string_view uri_too_long =
    R"({"detail":"The request line is longer than the server reads.","status":414,)"
    R"("title":"URI Too Long","type":"about:blank"})";
constexpr std::string_view headers_too_large =
    R"({"detail":"The request's headers are longer than the server reads.","status":431,)"
    R"("title":"Request Header Fields Too Large","type":"about:blank"})";
constexpr std::string_view not_implemented =
    R"({"detail":"A body sent with a Transfer-Encoding cannot be read.","status":501,)"
    R"("title":"Not Implemented","type":"about:blank"})";
constexpr std::string_view too_large =
    R"({"detail":"The request's body is larger than 1048576 bytes, the most the server takes.",)"
    R"("status":413,"title":"Content too large",)"
    R"("type":"https://libranet.org/problems/content-too-large"})";
constexpr std::string_view too_large_past_4 =
    R"({"detail":"The request's body is larger than 4 bytes, the most the server takes.",)"
    R"("status":413,"title":"Content too large",)"
    R"("type":"https://libranet.org/problems/content-too-large"})";

// The id of a node, the identifier of its public key (HTTP API §11.2): here
// the SHA-256 of nothing.
constexpr std::string_view node_id =
    "sha256/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

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

// The server's answer, as status says, with problem details, to a request
// for target on a connection it keeps open.
std::string problem(std::string_view status, std::string_view target,
                    std::string_view details) {
    return "HTTP/1.1 " + std::string(status) + "\r\nContent-Length: "
           + std::to_string(details.size())
           + "\r\nContent-Type: application/problem+json\r\nX-Request-Path: "
           + std::string(target) + "\r\n\r\n" + std::string(details);
}

// The server's answer, as status says, with problem details, to a request it
// refuses, closing the connection after it. A request whose line it cannot
// read has no target to name, given as "".
std::string refusal(std::string_view status, std::string_view target,
                    std::string_view details) {
    std::string text = "HTTP/1.1 " + std::string(status)
                       + "\r\nConnection: close\r\nContent-Length: "
                       + std::to_string(details.size())
                       + "\r\nContent-Type: application/problem+json\r\n";

    if (!target.empty()) {
        text += "X-Request-Path: " + std::string(target) + "\r\n";
    }

    return text + "\r\n" + std::string(details);
}

// A handler that answers with body.
Server::RequestHandler answer_with(std::string body) {
    return [body = std::move(body)](const Request& /*request*/, Response& response,
                                    ConnectionInfo& /*connection*/) {
        response.set_body(body);
    };
}

// A handler that throws, as storage's does for a signed request for /data/x,
// whose identifier has no hash.
void throw_invalid_identifier(const Request& /*request*/, Response& /*response*/,
                              ConnectionInfo& /*connection*/) {
    throw std::invalid_argument("Invalid identifier format: missing hash component.");
}

// A handler that answers with where the connection came from.
void answer_with_remote_address(
    const Request& /*request*/, Response& response,
    ConnectionInfo& connection) { // cppcheck-suppress constParameterCallback
    response.set_body(connection.remote_address().to_string());
}

// A handler that answers with where the connection reached.
void answer_with_local_address(
    const Request& /*request*/, Response& response,
    ConnectionInfo& connection) { // cppcheck-suppress constParameterCallback
    response.set_body(connection.local_address().to_string());
}

// A handler that answers with how many requests the connection has carried.
void answer_with_requests(const Request& /*request*/, Response& response,
                          ConnectionInfo& connection) { // cppcheck-suppress constParameterCallback
    response.set_body(std::to_string(connection.requests()));
}

// A handler that has the connection carry node_id's signature, as one
// checking signatures will once it has verified one.
void sign_as_node(const Request& /*request*/, Response& /*response*/, ConnectionInfo& connection) {
    connection.set_node(Identifier::parse(node_id));
}

// A handler that answers with the node whose signature the connection
// carries, or "none".
void answer_with_node(const Request& /*request*/, Response& response,
                      ConnectionInfo& connection) { // cppcheck-suppress constParameterCallback
    const auto& node = connection.node();

    response.set_body(node.has_value() ? node->to_string() : "none");
}

// A handler that has the connection closed once it is answered with "Bye".
void close_after_bye(const Request& /*request*/, Response& response, ConnectionInfo& connection) {
    connection.close_after_answer();
    response.set_body("Bye");
}

// A handler that stops server.
Server::RequestHandler stopping(Server& server) {
    return [&server](const Request& /*request*/, Response& /*response*/,
                     ConnectionInfo& /*connection*/) {
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
    // Makes the server again, at endpoint, before its handlers are
    // registered.
    void bind(const Address& endpoint);

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

void ServerTest::bind(const Address& endpoint) {
    m_server = std::make_unique<Server>(endpoint);
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

    EXPECT_EQ(ask(address(), get("/missing")), problem("404 Not Found", "/missing", not_found));
}

// A URI that only starts with the letters of a path, as /database does with
// /data, is not under it.
TEST_F(ServerTest, PathIsNotAPrefixOfALongerName) {
    server().register_handler("/data", answer_with("data"));
    start();

    EXPECT_EQ(ask(address(), get("/database")), problem("404 Not Found", "/database", not_found));
    EXPECT_EQ(ask(address(), get("/data-set")), problem("404 Not Found", "/data-set", not_found));
    EXPECT_EQ(ask(address(), get("/dat")), problem("404 Not Found", "/dat", not_found));
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
    server().register_handler("/echo", [](const Request& request, Response& response,
                                          ConnectionInfo& /*connection*/) {
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

    server().register_handler("/slow", [slow](const Request& /*request*/, Response& response,
                                              ConnectionInfo& /*connection*/) {
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

// A request saying ConnectionInfo: close, alone or in a list, in any case, is
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
    server().register_handler("/bye", [](const Request& /*request*/, Response& response,
                                         ConnectionInfo& /*connection*/) {
        response.headers().set("Connection", "close");
        response.set_body("Bye");
    });
    start();

    EXPECT_EQ(ask_until_closed(address(), get("/bye")),
              "HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 3\r\n"
              "X-Request-Path: /bye\r\n\r\nBye");
}

// A handler can have the connection closed once it is answered, which the
// answer says.
TEST_F(ServerTest, HandlerClosingTheConnectionClosesIt) {
    server().register_handler("/bye", close_after_bye);
    start();

    EXPECT_EQ(ask_until_closed(address(), get("/bye")),
              "HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 3\r\n"
              "X-Request-Path: /bye\r\n\r\nBye");
}

// A handler that has the connection closed, then throws, has it closed after
// the Internal Server Error it is answered with.
TEST_F(ServerTest, HandlerClosingTheConnectionThenThrowingClosesIt) {
    server().register_handler("/bye", [](const Request& request, Response& response,
                                         ConnectionInfo& connection) {
        connection.close_after_answer();
        throw_invalid_identifier(request, response, connection);
    });
    start();

    EXPECT_EQ(ask_until_closed(address(), get("/bye")),
              refusal("500 Internal Server Error", "/bye", internal_server_error));
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
    server().register_handler("/empty", [](const Request& /*request*/, Response& response,
                                           ConnectionInfo& /*connection*/) {
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

    EXPECT_EQ(ask_until_closed(address(), "\r\n"), refusal("400 Bad Request", "", bad_request));
}

// A Content-Length is digits alone.
TEST_F(ServerTest, ContentLengthNotDigitsIsBadRequest) {
    start();

    for (const auto* const length : {"five", "-1", "+5", "5 5", ""}) {
        EXPECT_EQ(ask_until_closed(address(), std::string("POST /x HTTP/1.1\r\nContent-Length: ")
                                                  + length + "\r\n\r\n"),
                  refusal("400 Bad Request", "/x", bad_request))
            << length;
    }
}

TEST_F(ServerTest, ContentLengthOutOfRangeIsPayloadTooLarge) {
    start();

    EXPECT_EQ(ask_until_closed(
                  address(), "POST /x HTTP/1.1\r\nContent-Length: 99999999999999999999999\r\n\r\n"),
              refusal("413 Payload Too Large", "/x", too_large));
}

// A body is taken up to the limit, and one past it is refused unread.
TEST_F(ServerTest, BodyPastTheLimitIsPayloadTooLarge) {
    constexpr std::size_t most_bytes = 4;
    Server::Limits limits;

    limits.body_bytes = most_bytes;
    limit(limits);
    server().register_handler("/echo", [](const Request& request, Response& response,
                                          ConnectionInfo& /*connection*/) {
        response.set_body(std::string(request.body()));
    });
    start();

    EXPECT_EQ(ask(address(), "POST /echo HTTP/1.1\r\nContent-Length: 4\r\n\r\nabcd"),
              answer("200 OK", "/echo", "abcd"));
    EXPECT_EQ(ask_until_closed(address(), "POST /echo HTTP/1.1\r\nContent-Length: 5\r\n\r\n"),
              refusal("413 Payload Too Large", "/echo", too_large_past_4));
}

// A body sent in chunks has no length to say where the next request starts.
TEST_F(ServerTest, ChunkedBodyIsNotImplemented) {
    start();

    EXPECT_EQ(ask_until_closed(address(),
                               "POST /x HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"),
              refusal("501 Not Implemented", "/x", not_implemented));
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
              refusal("414 URI Too Long", "", uri_too_long));
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
              refusal("431 Request Header Fields Too Large", "/hello", headers_too_large));
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
              problem("500 Internal Server Error", "/data/x", internal_server_error));
    connection->send_all(get("/hello"));
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/hello", "Hello"));
}

// The server answers as soon as it reads the header line, so the request
// ends there, leaving nothing unread.
TEST_F(ServerTest, HeaderWithoutColonIsBadRequest) {
    server().register_handler("/hello", answer_with("Hello"));
    start();

    EXPECT_EQ(ask_until_closed(address(), "GET /hello HTTP/1.1\r\nNo colon here\r\n"),
              refusal("400 Bad Request", "/hello", bad_request));
    EXPECT_EQ(ask(address(), get("/hello")), answer("200 OK", "/hello", "Hello"));
}

// A header set on every answer is carried by a handler's, in place of the one
// it gave, and by the server's own: for a path with no handler, when a
// handler throws, and to a request it refuses.
TEST_F(ServerTest, EveryAnswerCarriesTheHeaderSetOnIt) {
    server().register_handler("/hello", [](const Request& /*request*/, Response& response,
                                           ConnectionInfo& /*connection*/) {
        response.headers().set("X-Every", "the handler's");
        response.set_body("Hello");
    });
    server().register_handler("/data", throw_invalid_identifier);
    server().set_on_every_answer("X-Every", "the server's");
    start();

    const std::vector<std::string> answers{
        ask(address(), get("/hello")),
        ask(address(), get("/missing")),
        ask(address(), get("/data/x")),
        ask_until_closed(address(), "\r\n"),
        ask_until_closed(address(), "POST /x HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"),
    };

    for (const auto& answered : answers) {
        EXPECT_NE(answered.find("\r\nX-Every: the server's\r\n"), std::string::npos) << answered;
        EXPECT_EQ(answered.find("the handler's"), std::string::npos) << answered;
    }
}

// A client that leaves before its answer is sent ends only its own
// connection, and the server goes on to answer the next request.
TEST_F(ServerTest, ClientThatLeavesBeforeItsAnswerDoesNotStopTheServer) {
    // More than a connection holds unsent, so sending it fails once the
    // client is gone.
    constexpr std::size_t large_body_bytes = 16UL * 1024UL * 1024UL;
    std::promise<void> client_left;
    const auto left = client_left.get_future().share();

    server().register_handler("/large", [left](const Request& /*request*/, Response& response,
                                               ConnectionInfo& /*connection*/) {
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

// A handler is given the address and port the connection came from.
TEST_F(ServerTest, HandlerKnowsWhereTheConnectionCameFrom) {
    server().register_handler("/from", answer_with_remote_address);
    start();

    const auto connection = connect_when_listening(address());
    const auto from = connection->local_address().to_string();

    connection->send_all(get("/from"));
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/from", from));
}

// A handler is given the address and port of this machine the connection
// reached.
TEST_F(ServerTest, HandlerKnowsWhereTheConnectionReached) {
    server().register_handler("/to", answer_with_local_address);
    start();

    const auto connection = connect_when_listening(address());
    const auto reached = connection->remote_address().to_string();

    connection->send_all(get("/to"));
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/to", reached));
}

// A server bound to every address gives a handler the one the connection
// reached, not the one the server was bound to.
TEST_F(ServerTest, HandlerKnowsWhichOfEveryAddressTheConnectionReached) {
    bind(Address("0.0.0.0", 0));
    server().register_handler("/to", answer_with_local_address);
    start();

    const Address loopback("127.0.0.1", address().port());
    const auto connection = connect_when_listening(loopback);

    connection->send_all(get("/to"));
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/to", loopback.to_string()));
}

// Each connection counts every request it carries, the one being answered
// included, whichever handler answers it, or none.
TEST_F(ServerTest, HandlerKnowsHowManyRequestsTheConnectionCarried) {
    server().register_handler("/count", answer_with_requests);
    start();

    const auto connection = connect_when_listening(address());

    connection->send_all(get("/count") + get("/missing") + get("/count"));
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/count", "1"));
    EXPECT_EQ(read_answer(*connection), problem("404 Not Found", "/missing", not_found));
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/count", "3"));
    EXPECT_EQ(ask(address(), get("/count")), answer("200 OK", "/count", "1"));
}

// The node a handler finds the connection carries the signature of is known
// to the handlers of the requests after it on that connection, and on no
// other.
TEST_F(ServerTest, NodeTheConnectionCarriesIsKnownToItsLaterRequests) {
    server().register_handler("/sign", sign_as_node);
    server().register_handler("/node", answer_with_node);
    start();

    const auto connection = connect_when_listening(address());

    connection->send_all(get("/node") + get("/sign") + get("/node"));
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/node", "none"));
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/sign", ""));
    EXPECT_EQ(read_answer(*connection), answer("200 OK", "/node", node_id));
    EXPECT_EQ(ask(address(), get("/node")), answer("200 OK", "/node", "none"));
}
