#include <gtest/gtest.h>
#include <algorithm>
#include <array>
#include <cstddef>
#include <iterator>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "io/SocketReader.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"

using io::SocketReader;
using net::Address;
using net::ISocket;

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

// A socket that reads the text it is given, at most chunk_limit bytes a
// read. It cannot listen, accept, or say its addresses, and sends nothing.
class ScriptedSocket final : public ISocket {
public:
    ScriptedSocket(std::string input, std::size_t chunk_limit);

    void listen([[maybe_unused]] int backlog) override {
        throw std::logic_error("ScriptedSocket cannot listen");
    }

    [[nodiscard]] Ptr accept() override {
        throw std::logic_error("ScriptedSocket cannot accept");
    }

    std::size_t send([[maybe_unused]] std::string_view data) override {
        return 0;
    }

    bool receive(std::vector<std::byte>& buffer) override {
        const auto count = std::min({buffer.size(), m_chunk_limit, m_input.size() - m_read});
        const auto first = m_input.begin() + static_cast<std::string::difference_type>(m_read);

        buffer.resize(count);
        std::transform(first, first + static_cast<std::string::difference_type>(count),
                       buffer.begin(), [](char character) {
            return static_cast<std::byte>(character);
        });
        m_read += count;
        return count > 0;
    }

    [[nodiscard]] Address local_address() const override {
        throw std::logic_error("ScriptedSocket has no address");
    }

    [[nodiscard]] Address remote_address() const override {
        throw std::logic_error("ScriptedSocket has no address");
    }

    [[nodiscard]] bool is_open() const noexcept override {
        return true;
    }

    void set_receive_timeout([[maybe_unused]] Milliseconds timeout) override {}
    void set_send_timeout([[maybe_unused]] Milliseconds timeout) override {}
    void shutdown() override {}
    void close() override {}

private:
    std::string m_input;
    std::size_t m_read{0};
    std::size_t m_chunk_limit;
};

ScriptedSocket::ScriptedSocket(std::string input, std::size_t chunk_limit)
    : m_input(std::move(input))
    , m_chunk_limit(chunk_limit) {}

} // namespace

TEST(SocketReaderTest, ReadExactReadsAcrossPartialReceives) {
    constexpr auto size = 5;
    ScriptedSocket socket("abcdef", 2);
    SocketReader reader(socket);

    EXPECT_EQ(as_text(reader.read_exact(size)), "abcde");
}

TEST(SocketReaderTest, ReadExactStopsAtEof) {
    constexpr auto chunk_limit = 8;
    constexpr auto size = 10;
    ScriptedSocket socket("hi", chunk_limit);
    SocketReader reader(socket);

    EXPECT_EQ(as_text(reader.read_exact(size)), "hi");
}

// A size far larger than what is sent takes no more memory than is sent.
TEST(SocketReaderTest, ReadExactOfAnyLengthStopsAtEof) {
    ScriptedSocket socket("partial", 4);
    SocketReader reader(socket);

    EXPECT_EQ(as_text(reader.read_exact(std::numeric_limits<std::size_t>::max())), "partial");
}

TEST(SocketReaderTest, ReadUntilStopsAtDelimiter) {
    ScriptedSocket socket("alpha,beta,gamma", 3);
    SocketReader reader(socket);

    EXPECT_EQ(reader.read_until(','), "alpha,");
    EXPECT_EQ(reader.read_until(','), "beta,");
    EXPECT_EQ(reader.read_until(','), "gamma");
}

TEST(SocketReaderTest, ReadLineStripsCrLfAndLf) {
    ScriptedSocket socket("first\r\nsecond\nlast", 4);
    SocketReader reader(socket);

    EXPECT_EQ(reader.read_line(io::IReader::EolAction::MayHaveEol), "first");
    EXPECT_EQ(reader.read_line(io::IReader::EolAction::MayHaveEol), "second");
    EXPECT_EQ(reader.read_line(io::IReader::EolAction::MayHaveEol), "last");
    EXPECT_EQ(reader.read_line(io::IReader::EolAction::MayHaveEol), "");
}

TEST(SocketReaderTest, EmptySocketReturnsEmptyOnRead) {
    ScriptedSocket socket("", 4);
    SocketReader reader(socket);
    std::array<std::byte, 4> data{};

    EXPECT_EQ(reader.read_some(data.data(), 0), 0U);
    EXPECT_FALSE(reader.read_some(data.data(), data.size()).has_value());
    EXPECT_TRUE(reader.read_exact(4).empty());
    EXPECT_EQ(reader.read_line(io::IReader::EolAction::MayHaveEol), "");
}
