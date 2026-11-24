#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <cstddef>
#include <iterator>
#include <limits>
#include <stdexcept>
#include <string>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "net/SocketPosix.hpp"

using net::Address;
using net::ISocket;
using net::SocketPosix;
using std::chrono_literals::operator""ms;

namespace {

// The bytes as text, a char for each.
std::string as_text(const std::vector<std::byte>& bytes) {
    std::string text;
    text.reserve(bytes.size());
    std::transform(bytes.begin(), bytes.end(), std::back_inserter(text), [](std::byte byte) {
        return static_cast<char>(std::to_integer<unsigned char>(byte));
    });
    return text;
}

// Helper that binds to an ephemeral port and returns both the socket and the
// actual address that was assigned by the OS. It throws, failing the test,
// when bind() returns no socket, since GoogleTest's ASSERT_* cannot be used in
// a function that returns a value.
std::pair<ISocket::Ptr, Address> bind_ephemeral() {
    auto server = SocketPosix::bind(Address("127.0.0.1", 0));

    if (!server) {
        throw std::runtime_error("bind() returned no socket");
    }

    EXPECT_TRUE(server->is_open());

    const Address local = server->local_address();
    EXPECT_EQ(local.host(), "127.0.0.1");
    EXPECT_NE(local.port(), 0);

    return {std::move(server), local};
}

// A listening socket, a client connected to it, and the peer the listening
// socket accepted for the client. The client connects on this thread: the
// system completes the connection to a listening socket before accept().
struct Connection {
    ISocket::Ptr server;
    ISocket::Ptr client;
    ISocket::Ptr peer;
};

// A Connection on the loopback address.
Connection connect_ephemeral() {
    auto [server, address] = bind_ephemeral();

    server->listen(1);

    auto client = SocketPosix::connect(address);
    auto peer = server->accept();

    return {std::move(server), std::move(client), std::move(peer)};
}

} // namespace

TEST(SocketPosixTest, BindAndLocalAddress) {
    auto [server, address] = bind_ephemeral();
    EXPECT_TRUE(server->is_open());
    EXPECT_GT(address.port(), 0);

    // A second bind to an address a server is listening on must fail. The
    // server listens first: with SO_REUSEADDR, Linux lets two sockets bind the
    // same address until one of them listens, while macOS refuses either way.
    server->listen(1);
    EXPECT_THROW((void)SocketPosix::bind(address), std::system_error);

    server->close();
    EXPECT_FALSE(server->is_open());
}

TEST(SocketPosixTest, ListenAcceptConnect) {
    auto bound = bind_ephemeral();
    ISocket::Ptr server = std::move(bound.first);
    Address address = bound.second;

    server->listen(1);

    ISocket::Ptr client;
    std::thread connector([&] {
        client = SocketPosix::connect(address);
    });

    auto accepted = server->accept();
    connector.join();

    ASSERT_TRUE(client);
    ASSERT_TRUE(accepted);
    EXPECT_TRUE(client->is_open());
    EXPECT_TRUE(accepted->is_open());

    const Address client_local = client->local_address();
    const Address accepted_local = accepted->local_address();
    EXPECT_NE(client_local.port(), 0);
    EXPECT_NE(accepted_local.port(), 0);

    client->close();
    accepted->close();
    server->close();
}

TEST(SocketPosixTest, SendAndReceive) {
    auto bound = bind_ephemeral();
    ISocket::Ptr server = std::move(bound.first);
    Address address = bound.second;

    server->listen(1);

    ISocket::Ptr client;
    std::thread connector([&] {
        client = SocketPosix::connect(address);
    });

    auto peer = server->accept();
    connector.join();

    const std::string message = "Hello, Modern C++ Sockets!";
    const std::size_t sent = client->send(message);
    EXPECT_EQ(sent, message.size());

    // Test receiving into a std::vector<std::byte>
    constexpr std::size_t buffer_bytes = 64;
    std::vector<std::byte> buffer(buffer_bytes);
    const bool success = peer->receive(buffer);
    EXPECT_TRUE(success);

    // Convert bytes back to string for assertion
    const std::string received_message = as_text(buffer);
    EXPECT_EQ(received_message, message);

    client->close();
    peer->close();
    server->close();
}

TEST(SocketPosixTest, HelpersSendAllAndReadExact) {
    auto bound = bind_ephemeral();
    ISocket::Ptr server = std::move(bound.first);
    Address address = bound.second;

    server->listen(1);

    ISocket::Ptr client;
    std::thread connector([&] {
        client = SocketPosix::connect(address);
    });

    auto peer = server->accept();
    connector.join();

    const std::string payload = "BinaryPayloadData12345";
    client->send_all(payload);

    const std::vector<std::byte> exact_bytes = peer->read_exact(payload.size());
    EXPECT_EQ(exact_bytes.size(), payload.size());

    const std::string received_payload = as_text(exact_bytes);
    EXPECT_EQ(received_payload, payload);

    client->close();
    peer->close();
    server->close();
}

TEST(SocketPosixTest, ReadExactBeyondWhatArrives) {
    // A size far past what arrives returns what arrived, without first taking
    // that much memory.
    auto bound = bind_ephemeral();
    ISocket::Ptr server = std::move(bound.first);
    Address address = bound.second;

    server->listen(1);

    ISocket::Ptr client;
    std::thread connector([&] {
        client = SocketPosix::connect(address);
    });

    auto peer = server->accept();
    connector.join();

    client->send_all("partial");
    client->close();

    const std::vector<std::byte> bytes =
        peer->read_exact(std::numeric_limits<std::size_t>::max());

    EXPECT_EQ(as_text(bytes), "partial");

    peer->close();
    server->close();
}

TEST(SocketPosixTest, ReadExactLongerThanOnePiece) {
    constexpr std::size_t payload_bytes = 200000;
    const std::string payload(payload_bytes, 'x');
    auto bound = bind_ephemeral();
    ISocket::Ptr server = std::move(bound.first);
    Address address = bound.second;

    server->listen(1);

    ISocket::Ptr client;
    std::thread connector([&] {
        client = SocketPosix::connect(address);
    });

    auto peer = server->accept();
    connector.join();

    std::thread sender([&] {
        client->send_all(payload + "extra");
    });

    const std::vector<std::byte> bytes = peer->read_exact(payload_bytes);
    const std::vector<std::byte> extra_bytes = peer->read_exact(5);

    sender.join();

    EXPECT_EQ(as_text(bytes), payload);
    EXPECT_EQ(as_text(extra_bytes), "extra");

    client->close();
    peer->close();
    server->close();
}

TEST(SocketPosixTest, HelperReadLine) {
    auto bound = bind_ephemeral();
    ISocket::Ptr server = std::move(bound.first);
    Address address = bound.second;

    server->listen(1);

    ISocket::Ptr client;
    std::thread connector([&] {
        client = SocketPosix::connect(address);
    });

    auto peer = server->accept();
    connector.join();

    // Send HTTP-style lines with CRLF
    client->send_all("GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n");

    const std::string line1 = peer->read_line('\n');
    const std::string line2 = peer->read_line('\n');

    EXPECT_EQ(line1, "GET /index.html HTTP/1.1");
    EXPECT_EQ(line2, "Host: localhost");

    client->close();
    peer->close();
    server->close();
}

TEST(SocketPosixTest, CloseIsIdempotent) {
    auto [server, address] = bind_ephemeral();
    EXPECT_TRUE(server->is_open());

    server->close();
    EXPECT_FALSE(server->is_open());

    server->close();
    EXPECT_FALSE(server->is_open());
}

TEST(SocketPosixTest, OperationsOnClosedSocket) {
    auto [server, address] = bind_ephemeral();
    server->close();

    EXPECT_THROW(server->listen(1), std::logic_error);
    EXPECT_THROW((void)server->accept(), std::logic_error);
    EXPECT_THROW(server->send("test"), std::logic_error);
    constexpr std::size_t buffer_bytes = 10;
    std::vector<std::byte> buffer(buffer_bytes);
    EXPECT_THROW((void)server->receive(buffer), std::logic_error);
    EXPECT_THROW((void)server->local_address(), std::logic_error);
    EXPECT_THROW(server->shutdown(), std::logic_error);
}

TEST(SocketPosixTest, RemoteAddressIsThePeers) {
    const auto connection = connect_ephemeral();

    EXPECT_EQ(connection.client->remote_address().port(),
              connection.server->local_address().port());
    EXPECT_EQ(connection.peer->remote_address().port(),
              connection.client->local_address().port());
}

// The port is free once the socket bound to it is closed. A port bound but
// not listening would not do: macOS neither refuses nor answers a connection
// to it, which times out after some seconds.
TEST(SocketPosixTest, ConnectWhereNoneListensIsRefused) {
    auto [server, address] = bind_ephemeral();

    server->close();

    EXPECT_THROW((void)SocketPosix::connect(address), std::system_error);
}

// Connected with a timeout, the socket waits for what it reads as any does.
TEST(SocketPosixTest, ConnectWithinATimeout) {
    constexpr auto timeout = 10000ms;
    auto [server, address] = bind_ephemeral();

    server->listen(1);

    const auto client = SocketPosix::connect(address, timeout);
    const auto peer = server->accept();

    client->send_all("ping");
    EXPECT_EQ(as_text(peer->read_exact(4)), "ping");
    peer->send_all("pong");
    EXPECT_EQ(as_text(client->read_exact(4)), "pong");
}

// A listener that takes no connection holds as many as its queue has room
// for, and the next waits to be taken, until its timeout passes. Its
// backlog is 1: macOS takes 0 for the most it allows.
TEST(SocketPosixTest, ConnectTimesOut) {
    constexpr auto timeout = 100ms;
    constexpr auto most_tries = 16;
    auto [server, address] = bind_ephemeral();
    std::vector<ISocket::Ptr> held;
    std::error_code failure;

    server->listen(1);

    for (auto tries = 0; tries < most_tries && !failure; ++tries) {
        try {
            held.push_back(SocketPosix::connect(address, timeout));
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

TEST(SocketPosixTest, SendNothingSendsNothing) {
    const auto connection = connect_ephemeral();

    EXPECT_EQ(connection.client->send(""), 0U);
}

TEST(SocketPosixTest, ReceiveWithNoRoomReadsNothing) {
    const auto connection = connect_ephemeral();
    std::vector<std::byte> buffer;

    EXPECT_FALSE(connection.peer->receive(buffer));
}

// A timeout of 0 waits forever; the others are seconds and milliseconds.
TEST(SocketPosixTest, Timeouts) {
    constexpr auto both = 250ms;
    constexpr auto receive = 1500ms;
    const auto connection = connect_ephemeral();

    EXPECT_NO_THROW(connection.client->set_timeout(both));
    EXPECT_NO_THROW(connection.client->set_receive_timeout(receive));
    EXPECT_NO_THROW(connection.client->set_send_timeout(0ms));
    EXPECT_THROW(connection.client->set_receive_timeout(-1ms), std::invalid_argument);
    EXPECT_THROW(connection.client->set_send_timeout(-1ms), std::invalid_argument);
}

// With nothing sent to it, receive() gives up once its timeout has passed.
TEST(SocketPosixTest, ReceiveTimesOut) {
    constexpr auto timeout = 50ms;
    constexpr std::size_t buffer_bytes = 10;
    const auto connection = connect_ephemeral();
    std::vector<std::byte> buffer(buffer_bytes);

    connection.peer->set_receive_timeout(timeout);

    EXPECT_THROW((void)connection.peer->receive(buffer), std::system_error);
}

// A thread waiting to read a connection reads its end once the connection
// is shut down from another thread, with the socket still open.
TEST(SocketPosixTest, ShutdownWakesAThreadReading) {
    constexpr std::size_t buffer_bytes = 10;
    const auto connection = connect_ephemeral();
    std::vector<std::byte> buffer(buffer_bytes);
    bool received = true;
    std::thread reader([&] {
        received = connection.peer->receive(buffer);
    });

    connection.peer->shutdown();
    reader.join();

    EXPECT_FALSE(received);
    EXPECT_TRUE(connection.peer->is_open());
}

// The peer reads the end of the connection, and nothing more can be sent.
TEST(SocketPosixTest, ShutdownEndsTheConnectionBothWays) {
    constexpr std::size_t buffer_bytes = 10;
    const auto connection = connect_ephemeral();
    std::vector<std::byte> buffer(buffer_bytes);

    connection.peer->shutdown();

    EXPECT_FALSE(connection.client->receive(buffer));
    EXPECT_THROW((void)connection.peer->send("test"), std::system_error);
}

TEST(SocketPosixTest, ShutdownOnceThePeerHasGone) {
    const auto connection = connect_ephemeral();

    connection.client->close();

    EXPECT_NO_THROW(connection.peer->shutdown());
    EXPECT_NO_THROW(connection.peer->shutdown());
}

TEST(SocketPosixTest, ClosedSocketHasNoPeerOrTimeouts) {
    auto connection = connect_ephemeral();

    connection.client->close();

    EXPECT_THROW((void)connection.client->remote_address(), std::logic_error);
    EXPECT_THROW(connection.client->set_receive_timeout(0ms), std::logic_error);
    EXPECT_THROW(connection.client->set_send_timeout(0ms), std::logic_error);
}
