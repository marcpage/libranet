#pragma once

#include <algorithm>
#include <cstddef>
#include <iterator>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "IReader.hpp"

namespace io {

// Reads from another reader a buffer at a time, so that reading a line a
// byte at a time does not read the other a byte at a time. What it reads
// ahead is kept for its next read, so one is kept for as long as the input
// is read: a connection's, for each request on it.
class BufferedReader final : public IReader {
public:
    // Most requests' line and headers come in one read of this, and it is
    // small enough to keep one for each connection.
    static constexpr std::size_t default_buffer_size = 4096;

    explicit BufferedReader(IReader::Ptr upstream,
                            std::size_t buffer_size = default_buffer_size);

    [[nodiscard]] std::string read_until(char delimiter) override;
    [[nodiscard]] std::size_t buffered_bytes() const noexcept;

    [[nodiscard]] std::optional<std::size_t> read_some(std::byte* data, std::size_t size) override;

private:
    IReader::Ptr m_upstream;
    std::vector<std::byte> m_buffer;
    std::size_t m_position{0};
    std::size_t m_end{0};

    [[nodiscard]] bool _refill();
};

inline BufferedReader::BufferedReader(IReader::Ptr upstream, std::size_t buffer_size)
    : m_upstream(std::move(upstream))
    , m_buffer(buffer_size) {
    if (!m_upstream) {
        throw std::invalid_argument("BufferedReader requires a non-null upstream reader");
    }

    if (0 == buffer_size) {
        throw std::invalid_argument("BufferedReader buffer_size must be greater than zero");
    }
}

inline std::size_t BufferedReader::buffered_bytes() const noexcept {
    return m_end - m_position;
}

// Fills the buffer, in place of what it held, with what the upstream reader
// has. Returns false at the end of its input.
inline bool BufferedReader::_refill() {
    m_position = 0;
    m_end = 0;

    const auto read = m_upstream->read_some(m_buffer.data(), m_buffer.size());

    if (!read || 0 == *read) {
        return false;
    }

    m_end = *read;
    return true;
}

inline std::optional<std::size_t> BufferedReader::read_some(std::byte* data, std::size_t size) {
    if (0 == size) {
        return std::size_t{0};
    }

    if (m_position >= m_end) {
        // A read as large as the buffer gains nothing from it.
        if (size >= m_buffer.size()) {
            return m_upstream->read_some(data, size);
        }

        if (!_refill()) {
            return std::nullopt;
        }
    }

    const std::size_t to_copy = std::min(size, buffered_bytes());
    const auto first = m_buffer.begin() + static_cast<std::ptrdiff_t>(m_position);

    std::copy_n(first, to_copy, data);
    m_position += to_copy;
    return to_copy;
}

inline std::string BufferedReader::read_until(char delimiter) {
    std::string result;
    const auto target = static_cast<std::byte>(delimiter);

    while (m_position < m_end || _refill()) {
        const auto start = m_buffer.begin() + static_cast<std::ptrdiff_t>(m_position);
        const auto end = m_buffer.begin() + static_cast<std::ptrdiff_t>(m_end);
        const auto found = std::find(start, end, target);
        const auto taken = found == end ? end : std::next(found);

        std::transform(start, taken, std::back_inserter(result), [](std::byte byte) {
            return static_cast<char>(std::to_integer<unsigned char>(byte));
        });
        m_position += static_cast<std::size_t>(std::distance(start, taken));

        if (found != end) {
            break;
        }
    }

    return result;
}

} // namespace io
