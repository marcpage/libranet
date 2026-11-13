#include "ISocket.hpp"

#include <stdexcept>

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

ISocket::Ptr ISocket::connect(const Address& address) {
    return Socket::connect(address);
}

void ISocket::send_all(std::string_view data) {
    std::size_t total_sent = 0;

    while (total_sent < data.size()) {
        const auto remaining = data.substr(total_sent);
        const auto sent = send(remaining);

        if (sent == 0) {
            throw std::runtime_error("Connection closed unexpectedly during send_all");
        }

        total_sent += sent;
    }
}

std::vector<std::byte> ISocket::read_exact(std::size_t size) {
    std::vector<std::byte> total_buffer;

    total_buffer.reserve(size);

    while (total_buffer.size() < size) {
        const auto remaining = size - total_buffer.size();
        std::vector<std::byte> chunk(remaining);

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
    std::vector<std::byte> byte_buffer(1);

    while (true) {
        const auto success = receive(byte_buffer);

        if (!success || byte_buffer.empty()) {
            break; // EOF reached
        }

        const auto ch = static_cast<char>(byte_buffer[0]);

        if (ch == delimiter) {
            break;
        }

        if (ch == '\r') {
            continue;
        }

        line.push_back(ch);
    }

    return line;
}

} // net
