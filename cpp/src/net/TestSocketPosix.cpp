#include <iostream>
#include <cassert>
#include <string>
#include <vector>
#include <thread>
#include <chrono>
#include <stdexcept>

#include "SocketPosix.hpp"
#include "Address.hpp"

using namespace net;

namespace {

// Helper that binds to an ephemeral port and returns both the socket and the
// actual address that was assigned by the OS.
std::pair<ISocket::Ptr, Address> bind_ephemeral() {
    auto server = SocketPosix::bind(Address("127.0.0.1", 0));
    assert(server);
    assert(server->is_open());

    const Address local = server->local_address();
    assert(local.host() == "127.0.0.1");
    assert(local.port() != 0);

    return {std::move(server), local};
}

} // namespace

void test_bind_and_local_address() {
    std::cout << "-> Testing bind() and local_address()... " << std::flush;

    auto [server, addr] = bind_ephemeral();
    assert(server->is_open());
    assert(addr.port() > 0);

    bool caught = false;
    try {
        (void)SocketPosix::bind(addr);
    } catch (const std::system_error&) {
        caught = true;
    }
    assert(caught);

    server->close();
    assert(!server->is_open());

    std::cout << "PASSED\n";
}

void test_listen_accept_connect() {
    std::cout << "-> Testing listen / accept / connect... " << std::flush;

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

    assert(client);
    assert(accepted);
    assert(client->is_open());
    assert(accepted->is_open());

    const Address client_local = client->local_address();
    const Address accepted_local = accepted->local_address();
    assert(client_local.port() != 0);
    assert(accepted_local.port() != 0);

    client->close();
    accepted->close();
    server->close();

    std::cout << "PASSED\n";
}

void test_send_and_receive() {
    std::cout << "-> Testing send / receive (boolean stream API)... " << std::flush;

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
    assert(sent == message.size());

    // Test receiving into a std::vector<std::byte>
    std::vector<std::byte> buffer(64);
    bool success = peer->receive(buffer);
    assert(success);

    // Convert bytes back to string for assertion
    std::string received_msg(reinterpret_cast<const char*>(buffer.data()), buffer.size());
    assert(received_msg == message);

    client->close();
    peer->close();
    server->close();

    std::cout << "PASSED\n";
}

void test_helpers_send_all_and_read_exact() {
    std::cout << "-> Testing send_all() and read_exact()... " << std::flush;

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
    assert(exact_bytes.size() == payload.size());

    std::string received_payload(reinterpret_cast<const char*>(exact_bytes.data()), exact_bytes.size());
    assert(received_payload == payload);

    client->close();
    peer->close();
    server->close();

    std::cout << "PASSED\n";
}

void test_helper_read_line() {
    std::cout << "-> Testing read_line() (HTTP header style)... " << std::flush;

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

    assert(line1 == "GET /index.html HTTP/1.1");
    assert(line2 == "Host: localhost");

    client->close();
    peer->close();
    server->close();

    std::cout << "PASSED\n";
}

void test_close_is_idempotent() {
    std::cout << "-> Testing close() is idempotent... " << std::flush;

    auto [server, addr] = bind_ephemeral();
    assert(server->is_open());

    server->close();
    assert(!server->is_open());

    server->close();
    assert(!server->is_open());

    std::cout << "PASSED\n";
}

void test_operations_on_closed_socket() {
    std::cout << "-> Testing operations on a closed socket throw... " << std::flush;

    auto [server, addr] = bind_ephemeral();
    server->close();

    bool caught_listen = false;
    try {
        server->listen(1);
    } catch (const std::logic_error&) {
        caught_listen = true;
    }
    assert(caught_listen);

    bool caught_accept = false;
    try {
        (void)server->accept();
    } catch (const std::logic_error&) {
        caught_accept = true;
    }
    assert(caught_accept);

    bool caught_send = false;
    try {
        server->send("test");
    } catch (const std::logic_error&) {
        caught_send = true;
    }
    assert(caught_send);

    bool caught_receive = false;
    try {
        std::vector<std::byte> buf(10);
        (void)server->receive(buf);
    } catch (const std::logic_error&) {
        caught_receive = true;
    }
    assert(caught_receive);

    bool caught_local = false;
    try {
        (void)server->local_address();
    } catch (const std::logic_error&) {
        caught_local = true;
    }
    assert(caught_local);

    std::cout << "PASSED\n";
}

int main() {
#if defined(__linux__) || defined(__APPLE__)
    std::cout << "    Running Modern Socket Test Suite    \n";

    test_bind_and_local_address();
    test_listen_accept_connect();
    test_send_and_receive();
    test_helpers_send_all_and_read_exact();
    test_helper_read_line();
    test_close_is_idempotent();
    test_operations_on_closed_socket();

    std::cout << "   All Tests Passed Successfully!   \n";
#endif
    return 0;
}
