#pragma once

#include <algorithm>
#include <cstddef>
#include <optional>
#include <vector>

#include "IReader.hpp"
#include "net/ISocket.hpp"

namespace io {

// Reads from a socket it does not own, which must outlive it.
class SocketReader final : public IReader {
public:
    explicit SocketReader(net::ISocket& socket) noexcept;

    [[nodiscard]] std::optional<std::size_t> read_some(std::byte* data, std::size_t size) override;

private:
    net::ISocket* m_socket;
};

inline SocketReader::SocketReader(net::ISocket& socket) noexcept
    : m_socket(&socket) {}

inline std::optional<std::size_t> SocketReader::read_some(std::byte* data, std::size_t size) {
    if (0 == size) {
        return std::size_t{0};
    }

    std::vector<std::byte> chunk(size);

    if (!m_socket->receive(chunk)) {
        return std::nullopt;
    }

    std::copy_n(chunk.begin(), chunk.size(), data);
    return chunk.size();
}

} // namespace io
