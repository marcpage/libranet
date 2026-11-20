#pragma once

#include <algorithm>
#include <cstddef>
#include <optional>
#include <vector>

#include "io/IReader.hpp"
#include "net/ISocket.hpp"

namespace io {

class SocketReader final : public IReader {
public:
    explicit SocketReader(net::ISocket& socket) noexcept;

    std::optional<std::size_t> read_some(std::byte* data, std::size_t size) override;

private:
    net::ISocket& m_socket;
};

inline SocketReader::SocketReader(net::ISocket& socket) noexcept
    : m_socket(socket) {}

inline std::optional<std::size_t> SocketReader::read_some(std::byte* data, std::size_t size) {
    if (size == 0) {
        return std::size_t{0};
    }

    std::vector<std::byte> chunk(size);

    if (!m_socket.receive(chunk)) { //[cite: 1, 2]
        return std::nullopt;
    }

    std::copy_n(chunk.begin(), chunk.size(), data);
    return chunk.size();
}

} // namespace io