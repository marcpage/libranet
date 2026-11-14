#include <gtest/gtest.h>
#include <chrono>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "net/Address.hpp"
#include "net/SocketPosix.hpp"

using namespace net;

namespace {

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

} // namespace

TEST(SocketPosixTest, BindAndLocalAddress) {
    auto [server, addr] = bind_ephemeral();
    EXPECT_TRUE(server->is_open());
    EXPECT_GT(addr.port(), 0);

    // A second bind to an address a server is listening on must fail. The
    // server listens first: with SO_REUSEADDR, Linux lets two sockets bind
    // the same address until one of them listens, while macOS refuses either way.
    server->listen(1);
    EXPECT_THROW((void)SocketPosix::bind(addr), std::system_error);

    server->close();
    EXPECT_FALSE(server->is_open());
}

TEST(SocketPosixTest, ListenAcceptConnect) {
    auto bound = bind_ephemeral();
    ISocket::Ptr server = std::move(bound.first);
    Address addr = bound.second;

    server->listen(1);

    ISocket::Ptr client;
    std::thread connector([&] {
        client = SocketPosix::connect(addr);
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
    Address addr = bound.second;

    server->listen(1);

    ISocket::Ptr client;
    std::thread connector([&] {
        client = SocketPosix::connect(addr);
    });

    auto peer = server->accept();
    connector.join();

    const std::string message = "Hello, Modern C++ Sockets!";
    std::size_t sent = client->send(message);
    EXPECT_EQ(sent, message.size());

    // Test receiving into a std::vector<std::byte>
    constexpr std::size_t buffer_bytes = 64;
    std::vector<std::byte> buffer(buffer_bytes);
    bool success = peer->receive(buffer);
    EXPECT_TRUE(success);

    // Convert bytes back to string for assertion
    std::string received_msg(reinterpret_cast<const char*>(buffer.data()), buffer.size());
    EXPECT_EQ(received_msg, message);

    client->close();
    peer->close();
    server->close();
}

TEST(SocketPosixTest, HelpersSendAllAndReadExact) {
    auto bound = bind_ephemeral();
    ISocket::Ptr server = std::move(bound.first);
    Address addr = bound.second;

    server->listen(1);

    ISocket::Ptr client;
    std::thread connector([&] {
        client = SocketPosix::connect(addr);
    });

    auto peer = server->accept();
    connector.join();

    const std::string payload = "BinaryPayloadData12345";
    client->send_all(payload);

    std::vector<std::byte> exact_bytes = peer->read_exact(payload.size());
    EXPECT_EQ(exact_bytes.size(), payload.size());

    std::string received_payload(reinterpret_cast<const char*>(exact_bytes.data()), exact_bytes.size());
    EXPECT_EQ(received_payload, payload);

    client->close();
    peer->close();
    server->close();
}

TEST(SocketPosixTest, HelperReadLine) {
    auto bound = bind_ephemeral();
    ISocket::Ptr server = std::move(bound.first);
    Address addr = bound.second;

    server->listen(1);

    ISocket::Ptr client;
    std::thread connector([&] {
        client = SocketPosix::connect(addr);
    });

    auto peer = server->accept();
    connector.join();

    // Send HTTP-style lines with CRLF
    client->send_all("GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n");

    std::string line1 = peer->read_line('\n');
    std::string line2 = peer->read_line('\n');

    EXPECT_EQ(line1, "GET /index.html HTTP/1.1");
    EXPECT_EQ(line2, "Host: localhost");

    client->close();
    peer->close();
    server->close();
}

TEST(SocketPosixTest, CloseIsIdempotent) {
    auto [server, addr] = bind_ephemeral();
    EXPECT_TRUE(server->is_open());

    server->close();
    EXPECT_FALSE(server->is_open());

    server->close();
    EXPECT_FALSE(server->is_open());
}

TEST(SocketPosixTest, OperationsOnClosedSocket) {
    auto [server, addr] = bind_ephemeral();
    server->close();

    EXPECT_THROW(server->listen(1), std::logic_error);
    EXPECT_THROW((void)server->accept(), std::logic_error);
    EXPECT_THROW(server->send("test"), std::logic_error);
    constexpr std::size_t buffer_bytes = 10;
    std::vector<std::byte> buf(buffer_bytes);
    EXPECT_THROW((void)server->receive(buf), std::logic_error);
    EXPECT_THROW((void)server->local_address(), std::logic_error);
}
