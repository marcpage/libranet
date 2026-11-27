#include <gtest/gtest.h>
#include <algorithm>
#include <cstddef>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "types/Converter.hpp"

using net::Address;
using net::ISocket;
using types::Converter;
using std::chrono_literals::operator""ms;

namespace {

// A socket that reads the text it is given, as much as fits each read, and
// keeps what is sent to it, at most a set number of bytes at a time. It
// keeps the timeouts it is given. It cannot listen, accept, or say its
// addresses.
class ScriptedSocket : public ISocket {
public:
    ScriptedSocket(std::string input, std::size_t send_limit);

    [[nodiscard]] const std::string& sent() const noexcept;
    [[nodiscard]] Milliseconds receive_timeout() const noexcept;
    [[nodiscard]] Milliseconds send_timeout() const noexcept;

    void listen(int backlog) override;
    [[nodiscard]] Ptr accept() override;
    std::size_t send(std::string_view data) override;
    bool receive(std::vector<std::byte>& buffer) override;
    [[nodiscard]] Address local_address() const override;
    [[nodiscard]] Address remote_address() const override;
    [[nodiscard]] bool is_open() const noexcept override;
    void set_receive_timeout(Milliseconds timeout) override;
    void set_send_timeout(Milliseconds timeout) override;
    void shutdown() override;
    void close() override;

private:
    std::string m_input;
    std::size_t m_read = 0;
    std::size_t m_send_limit;
    std::string m_sent;
    Milliseconds m_receive_timeout{};
    Milliseconds m_send_timeout{};
};

ScriptedSocket::ScriptedSocket(std::string input, std::size_t send_limit)
    : m_input(std::move(input))
    , m_send_limit(send_limit) {}

const std::string& ScriptedSocket::sent() const noexcept {
    return m_sent;
}

ISocket::Milliseconds ScriptedSocket::receive_timeout() const noexcept {
    return m_receive_timeout;
}

ISocket::Milliseconds ScriptedSocket::send_timeout() const noexcept {
    return m_send_timeout;
}

void ScriptedSocket::listen([[maybe_unused]] int backlog) {
    throw std::logic_error("ScriptedSocket cannot listen");
}

ISocket::Ptr ScriptedSocket::accept() {
    throw std::logic_error("ScriptedSocket cannot accept");
}

std::size_t ScriptedSocket::send(std::string_view data) {
    const auto count = std::min(data.size(), m_send_limit);

    m_sent.append(data.substr(0, count));
    return count;
}

bool ScriptedSocket::receive(std::vector<std::byte>& buffer) {
    const auto count = std::min(buffer.size(), m_input.size() - m_read);
    const auto first = m_input.begin() + static_cast<std::string::difference_type>(m_read);

    buffer.resize(count);
    std::transform(first, first + static_cast<std::string::difference_type>(count),
                   buffer.begin(), [](char character) {
        return static_cast<std::byte>(character);
    });
    m_read += count;
    return count > 0;
}

Address ScriptedSocket::local_address() const {
    throw std::logic_error("ScriptedSocket has no address");
}

Address ScriptedSocket::remote_address() const {
    throw std::logic_error("ScriptedSocket has no address");
}

bool ScriptedSocket::is_open() const noexcept {
    return true;
}

void ScriptedSocket::set_receive_timeout(Milliseconds timeout) {
    m_receive_timeout = timeout;
}

void ScriptedSocket::set_send_timeout(Milliseconds timeout) {
    m_send_timeout = timeout;
}

void ScriptedSocket::shutdown() {}

void ScriptedSocket::close() {}

} // namespace

TEST(ISocketTest, SendAllSendsInPieces) {
    constexpr std::size_t send_limit = 3;
    ScriptedSocket socket("", send_limit);

    socket.send_all("hello world");

    EXPECT_EQ(socket.sent(), "hello world");
}

TEST(ISocketTest, SendAllRefusesWhenNothingIsSent) {
    ScriptedSocket socket("", 0);

    EXPECT_THROW(socket.send_all("hello"), std::runtime_error);
}

TEST(ISocketTest, ReadExactStopsAtTheSize) {
    constexpr std::size_t size = 2;
    ScriptedSocket socket("abc", 0);

    EXPECT_EQ(Converter::text_of(socket.read_exact(size)), "ab");
}

TEST(ISocketTest, ReadExactStopsAtTheEndOfInput) {
    constexpr std::size_t size = 10;
    ScriptedSocket socket("abc", 0);

    EXPECT_EQ(Converter::text_of(socket.read_exact(size)), "abc");
}

// A line's carriage returns are dropped, and a last line without the
// delimiter is still a line.
TEST(ISocketTest, ReadLineToTheDelimiterOrTheEnd) {
    ScriptedSocket socket("first\r\nlast", 0);
    std::string line;

    EXPECT_EQ(socket.read_line(), "first");
    EXPECT_TRUE(socket.read_line(line));
    EXPECT_EQ(line, "last");
    EXPECT_FALSE(socket.read_line(line));
    EXPECT_EQ(line, "");
}

TEST(ISocketTest, ReadLineToAnotherDelimiter) {
    ScriptedSocket socket("first;second", 0);

    EXPECT_EQ(socket.read_line(';'), "first");
    EXPECT_EQ(socket.read_line(';'), "second");
}

TEST(ISocketTest, SetTimeoutSetsBoth) {
    constexpr auto timeout = 250ms;
    ScriptedSocket socket("", 0);

    socket.set_timeout(timeout);

    EXPECT_EQ(socket.receive_timeout(), timeout);
    EXPECT_EQ(socket.send_timeout(), timeout);
}

TEST(ISocketTest, BindAndConnectMakeThePlatformsSockets) {
    const auto server = ISocket::bind(Address("127.0.0.1", 0));
    ASSERT_NE(server, nullptr);
    server->listen(1);

    const auto client = ISocket::connect(server->local_address());
    ASSERT_NE(client, nullptr);

    EXPECT_TRUE(client->is_open());
}
