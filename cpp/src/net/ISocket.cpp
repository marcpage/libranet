#include "ISocket.hpp"

#include <algorithm>
#include <cstddef>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "Address.hpp"

#if defined(__linux__) || defined(__APPLE__)
#include "SocketPosix.hpp"
using Socket = net::SocketPosix;
#else
#error "Unsupported Platform"
#endif

namespace net {

ISocket::Ptr ISocket::bind(const Address& address) {
    return Socket::bind(address);
}

ISocket::Ptr ISocket::connect(const Address& address, Milliseconds timeout) {
    return Socket::connect(address, timeout);
}

void ISocket::send_all(std::string_view data) {
    std::size_t total_sent = 0;

    while (total_sent < data.size()) {
        const auto remaining = data.substr(total_sent);
        const auto sent = send(remaining);

        if (0 == sent) {
            throw std::runtime_error("Connection closed unexpectedly during send_all");
        }

        total_sent += sent;
    }
}

// Reads size bytes a piece at a time, so that a size larger than what the
// peer sends takes no more memory than the peer sends.
std::vector<std::byte> ISocket::read_exact(std::size_t size) {
    constexpr std::size_t piece_bytes = 65536;
    std::vector<std::byte> total_buffer;
    std::vector<std::byte> chunk;

    while (total_buffer.size() < size) {
        chunk.resize(std::min(piece_bytes, size - total_buffer.size()));

        const auto success = receive(chunk);

        if (!success || chunk.empty()) {
            break; // EOF reached early
        }

        total_buffer.insert(total_buffer.end(), chunk.begin(), chunk.end());
    }

    return total_buffer;
}

std::string ISocket::read_line(char delimiter) {
    std::string line;

    (void)read_line(line, delimiter);

    return line;
}

bool ISocket::read_line(std::string& line, char delimiter) {
    std::vector<std::byte> byte_buffer(1);

    line.clear();

    while (true) {
        const auto success = receive(byte_buffer);

        if (!success || byte_buffer.empty()) {
            return !line.empty();
        }

        const auto character = static_cast<char>(byte_buffer.at(0));

        if (character == delimiter) {
            return true;
        }

        if ('\r' == character) {
            continue;
        }

        line.push_back(character);
    }

    return true;
}

} // namespace net
