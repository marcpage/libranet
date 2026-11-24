#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <cstddef>
#include <exception>
#include <functional>
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
#include "io/MeteredReader.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "peer/PeerConnection.hpp"

using http::Request;
using http::Response;
using net::Address;
using net::ISocket;
using peer::PeerConnection;
using std::chrono_literals::operator""ms;

namespace {

// How long to wait, at most, for what a test waits on, so that a test fails
// rather than waits forever when it does not come; and how long between
// looks.
constexpr auto patience = std::chrono::seconds(10);
constexpr auto look_interval = 5ms;

// A request timeout short enough for a test to wait out.
constexpr auto short_timeout = 100ms;

// The header that gives a body's length, as a line of a request's head.
constexpr std::string_view length_line = "Content-Length: ";

// The bytes as text, a char for each.
std::string as_text(const std::vector<std::byte>& bytes) {
    std::string text;

    std::transform(bytes.begin(), bytes.end(), std::back_inserter(text), [](std::byte byte) {
        return static_cast<char>(std::to_integer<unsigned char>(byte));
    });
    return text;
}

// The next request on socket, as sent: its line and headers, each ended
// with a CRLF, and its body, as long as its Content-Length gives.
std::string read_request(ISocket& socket) {
    std::string text;
    std::string line;
    std::size_t length = 0;

    while (socket.read_line(line) && !line.empty()) {
        text += line + "\r\n";

        if (0 == line.rfind(length_line, 0)) {
            length = std::stoul(line.substr(length_line.size()));
        }
    }

    return text + "\r\n" + as_text(socket.read_exact(length));
}

// An answer of 200 OK with the body, and the headers, each ended with a
// CRLF.
std::string ok(const std::string& body, const std::string& headers = "") {
    return "HTTP/1.1 200 OK\r\n" + headers + std::string(length_line)
           + std::to_string(body.size()) + "\r\n\r\n" + body;
}

// Waits until the connection closes.
void wait_for_end(ISocket& socket) {
    std::string line;

    while (socket.read_line(line)) {
    }
}

// A node on the loopback address that takes one connection, and plays its
// part on the connection on a thread of its own. The connection closes once
// it has played it.
class StandInNode {
public:
    using Part = std::function<void(ISocket& connection)>;

    explicit StandInNode(Part part);
    ~StandInNode();

    [[nodiscard]] Address address() const;

    StandInNode(const StandInNode&) = delete;
    StandInNode& operator=(const StandInNode&) = delete;
    StandInNode(StandInNode&&) = delete;
    StandInNode& operator=(StandInNode&&) = delete;

private:
    ISocket::Ptr m_listener;
    std::thread m_thread;
};

StandInNode::StandInNode(Part part)
    : m_listener(ISocket::bind(Address("127.0.0.1", 0))) {
    m_listener->listen(1);
    m_thread = std::thread([this, part = std::move(part)] {
        try {
            const auto connection = m_listener->accept();

            connection->set_receive_timeout(patience);
            part(*connection);
        } catch (const std::exception& error) {
            ADD_FAILURE() << "The node failed to play its part: " << error.what();
        }
    });
}

StandInNode::~StandInNode() {
    m_thread.join();
}

Address StandInNode::address() const {
    return m_listener->local_address();
}

// The node's part: it reads count requests, and only then answers each, in
// turn, with its text as the body; then it waits for this node to close
// the connection.
StandInNode::Part reads_then_echoes(std::size_t count) {
    return [count](ISocket& connection) {
        std::vector<std::string> requests;

        std::generate_n(std::back_inserter(requests), count, [&connection] {
            return read_request(connection);
        });

        for (const auto& request : requests) {
            connection.send_all(ok(request));
        }

        wait_for_end(connection);
    };
}

// The node's part: it reads count requests, and closes the connection
// without answering.
StandInNode::Part reads_then_closes(std::size_t count) {
    return [count](ISocket& connection) {
        for (std::size_t index = 0; index < count; ++index) {
            (void)read_request(connection);
        }
    };
}

// The node's part: it reads count requests, sends what it is given, and
// waits for this node to close the connection.
StandInNode::Part reads_then_sends(std::size_t count, std::string answers) {
    return [count, answers = std::move(answers)](ISocket& connection) {
        for (std::size_t index = 0; index < count; ++index) {
            (void)read_request(connection);
        }

        connection.send_all(answers);
        wait_for_end(connection);
    };
}

// A request with the method, target, and body.
Request request(const std::string& method, const std::string& target,
                const std::string& body = "") {
    Request made;

    made.set_method(method);
    made.set_uri(target);
    made.set_body(body);
    return made;
}

// The answer to come, once it has come. Throws, failing the test, if it
// takes longer than a test waits, and what it fails with if it fails.
Response answer_of(std::future<Response>& answer) {
    if (std::future_status::ready != answer.wait_for(patience)) {
        throw std::runtime_error("No answer came");
    }

    return answer.get();
}

// Whether the connection closes before a test gives up on it.
bool closes(const PeerConnection& connection) {
    const auto deadline = std::chrono::steady_clock::now() + patience;

    while (connection.is_open() && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(look_interval);
    }

    return !connection.is_open();
}

// Whether the connection turns the request down as one it cannot send.
bool refused(PeerConnection& connection, Request turned_down) {
    try {
        (void)connection.send(std::move(turned_down));
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

// Limits with a request timeout of timeout.
PeerConnection::Limits waiting(PeerConnection::Milliseconds timeout) {
    PeerConnection::Limits limits;

    limits.request_timeout = timeout;
    return limits;
}

} // namespace

// The node reads all three requests before it answers any, so they were
// sent without waiting for answers, and each answer goes to its request.
TEST(PeerConnectionTest, RequestsAreSentWithoutWaitingAndAnsweredInTurn) {
    const StandInNode node(reads_then_echoes(3));
    PeerConnection connection(node.address(), {});

    auto first = connection.send(request("GET", "/first"));
    auto second = connection.send(request("GET", "/second"));
    auto third = connection.send(request("GET", "/third"));

    EXPECT_EQ(answer_of(first).body().substr(0, 15), "GET /first HTTP");
    EXPECT_EQ(answer_of(second).body().substr(0, 16), "GET /second HTTP");
    EXPECT_EQ(answer_of(third).body().substr(0, 15), "GET /third HTTP");
    EXPECT_TRUE(connection.is_open());
    EXPECT_FALSE(connection.was_turned_away());
}

// Each request names the node in Host, and gives the length of a body, or
// of none where its method expects one.
TEST(PeerConnectionTest, RequestsAreFramed) {
    const StandInNode node(reads_then_echoes(3));
    PeerConnection connection(node.address(), {});
    const auto host = "Host: " + node.address().to_string() + "\r\n";
    auto get = request("GET", "/data?x=1");

    get.headers().set("Content-Length", "9");
    get.headers().set("Host", "elsewhere");

    auto got = connection.send(get);
    auto put = connection.send(request("PUT", "/data/a", "hello"));
    auto posted = connection.send(request("POST", "/data/nodes"));

    EXPECT_EQ(answer_of(got).body(), "GET /data?x=1 HTTP/1.1\r\n" + host + "\r\n");
    EXPECT_EQ(answer_of(put).body(),
              "PUT /data/a HTTP/1.1\r\nContent-Length: 5\r\n" + host + "\r\nhello");
    EXPECT_EQ(answer_of(posted).body(),
              "POST /data/nodes HTTP/1.1\r\nContent-Length: 0\r\n" + host + "\r\n");
}

// The answer saying Connection: close is given to its request, and the
// request after it fails, as it will never be answered.
TEST(PeerConnectionTest, ConnectionCloseTurnsThisNodeAway) {
    const StandInNode node(reads_then_sends(2, ok("bye", "Connection: close\r\n")));
    PeerConnection connection(node.address(), {});

    auto answered = connection.send(request("GET", "/first"));
    auto unanswered = connection.send(request("GET", "/second"));

    EXPECT_EQ(answer_of(answered).body(), "bye");
    EXPECT_TRUE(connection.was_turned_away());
    EXPECT_FALSE(connection.is_open());
    EXPECT_THROW((void)answer_of(unanswered), PeerConnection::Closed);
    EXPECT_THROW((void)connection.send(request("GET", "/third")), PeerConnection::Closed);
}

// A node that closes the connection without answering does not turn this
// node away; every request waiting fails.
TEST(PeerConnectionTest, ClosingWithoutAnsweringFailsEveryRequest) {
    const StandInNode node(reads_then_closes(2));
    PeerConnection connection(node.address(), {});

    auto first = connection.send(request("GET", "/first"));
    auto second = connection.send(request("GET", "/second"));

    EXPECT_THROW((void)answer_of(first), PeerConnection::Closed);
    EXPECT_THROW((void)answer_of(second), PeerConnection::Closed);
    EXPECT_FALSE(connection.was_turned_away());
    EXPECT_FALSE(connection.is_open());
}

// The oldest request fails with why the answer could not be read, and the
// others with the connection closing.
TEST(PeerConnectionTest, UnreadableAnswerFailsEveryRequest) {
    const StandInNode node(reads_then_sends(2, "NOT HTTP\r\n\r\n"));
    PeerConnection connection(node.address(), {});

    auto first = connection.send(request("GET", "/first"));
    auto second = connection.send(request("GET", "/second"));

    EXPECT_THROW((void)answer_of(first), std::invalid_argument);
    EXPECT_THROW((void)answer_of(second), PeerConnection::Closed);
    EXPECT_FALSE(connection.is_open());
}

TEST(PeerConnectionTest, AnswerTooLargeFails) {
    const StandInNode node(reads_then_sends(1, ok("hello")));
    auto limits = waiting(PeerConnection::default_request_timeout);

    limits.answer.body_bytes = 4;

    PeerConnection connection(node.address(), limits);
    auto answer = connection.send(request("GET", "/large"));

    EXPECT_THROW((void)answer_of(answer), io::LimitExceeded);
}

TEST(PeerConnectionTest, AnswerNothingAskedForCloses) {
    const StandInNode node(reads_then_sends(0, ok("unasked")));
    PeerConnection connection(node.address(), {});

    EXPECT_TRUE(closes(connection));
    EXPECT_THROW((void)connection.send(request("GET", "/late")), PeerConnection::Closed);
}

// Interim answers come before the final one (RFC 9110 §15.2).
TEST(PeerConnectionTest, InterimAnswersAreSkipped) {
    const StandInNode node(reads_then_sends(1, "HTTP/1.1 100 Continue\r\n\r\n" + ok("done")));
    PeerConnection connection(node.address(), {});

    auto answer = connection.send(request("GET", "/data"));

    EXPECT_EQ(answer_of(answer).body(), "done");
    EXPECT_TRUE(connection.is_open());
}

TEST(PeerConnectionTest, NoAnswerWithinTheRequestTimeoutFails) {
    const StandInNode node(reads_then_sends(1, ""));
    PeerConnection connection(node.address(), waiting(short_timeout));
    auto answer = connection.send(request("GET", "/slow"));

    try {
        (void)answer_of(answer);
        ADD_FAILURE() << "An answer came";
    } catch (const std::system_error& error) {
        EXPECT_EQ(error.code(), std::make_error_code(std::errc::timed_out));
    }

    EXPECT_FALSE(connection.is_open());
}

// The request timeout counts only while a request waits: a connection with
// none waiting stays open past it.
TEST(PeerConnectionTest, IdleConnectionStaysOpen) {
    constexpr auto idle = short_timeout * 3;
    const StandInNode node(reads_then_echoes(1));
    PeerConnection connection(node.address(), waiting(short_timeout));

    std::this_thread::sleep_for(idle);

    ASSERT_TRUE(connection.is_open());

    auto answer = connection.send(request("GET", "/later"));

    EXPECT_EQ(answer_of(answer).body().substr(0, 15), "GET /later HTTP");
}

TEST(PeerConnectionTest, CloseFailsWaitingRequests) {
    const StandInNode node(reads_then_sends(1, ""));
    PeerConnection connection(node.address(), {});
    auto answer = connection.send(request("GET", "/never"));

    connection.close();

    EXPECT_FALSE(connection.is_open());
    EXPECT_FALSE(connection.was_turned_away());
    EXPECT_THROW((void)answer_of(answer), PeerConnection::Closed);
    EXPECT_THROW((void)connection.send(request("GET", "/after")), PeerConnection::Closed);
    EXPECT_NO_THROW(connection.close());
}

// Requests whose answers could not be told from the next one's.
TEST(PeerConnectionTest, RequestsThatCannotBeFramedAreRefused) {
    const StandInNode node(wait_for_end);
    PeerConnection connection(node.address(), {});
    auto chunked = request("POST", "/data/nodes", "body");
    auto broken = request("GET", "/data");
    auto unnamed = request("GET", "/data");

    chunked.headers().set("Transfer-Encoding", "chunked");
    broken.headers().set("X-Split", "one\r\nX-Other: two");
    unnamed.headers().set("", "nameless");

    EXPECT_TRUE(refused(connection, request("HEAD", "/data")));
    EXPECT_TRUE(refused(connection, request("G T", "/data")));
    EXPECT_TRUE(refused(connection, request("GET", "data")));
    EXPECT_TRUE(refused(connection, request("GET", "/da ta")));
    EXPECT_TRUE(refused(connection, chunked));
    EXPECT_TRUE(refused(connection, broken));
    EXPECT_TRUE(refused(connection, unnamed));
    EXPECT_TRUE(connection.is_open());
}

TEST(PeerConnectionTest, TimeoutsMustBePositive) {
    const Address nowhere("127.0.0.1", 1);
    PeerConnection::Limits no_connect;
    PeerConnection::Limits no_request;

    no_connect.connect_timeout = 0ms;
    no_request.request_timeout = -1ms;

    EXPECT_THROW((void)PeerConnection(nowhere, no_connect), std::invalid_argument);
    EXPECT_THROW((void)PeerConnection(nowhere, no_request), std::invalid_argument);
}

// A listener that takes no connection holds as many as its queue has room
// for, and the next waits to be taken, until the connect timeout passes.
// Its backlog is 1: macOS takes 0 for the most it allows.
TEST(PeerConnectionTest, ConnectTimesOut) {
    constexpr auto most_tries = 16;
    const auto listener = ISocket::bind(Address("127.0.0.1", 0));
    PeerConnection::Limits limits;
    std::vector<std::unique_ptr<PeerConnection>> held;
    std::error_code failure;

    limits.connect_timeout = short_timeout;
    listener->listen(1);

    for (auto tries = 0; tries < most_tries && !failure; ++tries) {
        try {
            held.push_back(std::make_unique<PeerConnection>(listener->local_address(), limits));
        } catch (const std::system_error& error) {
            failure = error.code();
        }
    }

    // Some systems, such as macOS 27, answer a connect past the full queue
    // at once and then reset it, so none waits to time out.
    if (failure != std::errc::timed_out) {
        GTEST_SKIP() << "A connect past the full queue was answered, not left to time out: "
                     << failure.message();
    }
}
