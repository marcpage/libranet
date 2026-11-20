#pragma once

#include <algorithm>
#include <cstddef>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "io/IReader.hpp"

namespace io {

class BufferedReader final : public IReader {
public:
    static constexpr std::size_t default_buffer_size = 4096;

    explicit BufferedReader(IReader::Ptr upstream,
                            std::size_t buffer_size = default_buffer_size);

    [[nodiscard]] std::string read_until(char delimiter);
    [[nodiscard]] std::string read_line();
    [[nodiscard]] std::size_t buffered_bytes() const noexcept;

protected:
    std::optional<std::size_t> read_some(std::byte* data, std::size_t size) override;

private:
    bool refill();

    IReader::Ptr m_upstream;
    std::vector<std::byte> m_buffer;
    std::size_t m_pos{0};
    std::size_t m_end{0};
};

inline BufferedReader::BufferedReader(IReader::Ptr upstream, std::size_t buffer_size)
    : m_upstream(std::move(upstream))
    , m_buffer(buffer_size) {
    if (!m_upstream) {
        throw std::invalid_argument("BufferedReader requires a non-null upstream reader.");
    }

    if (buffer_size == 0) {
        throw std::invalid_argument("BufferedReader buffer_size must be greater than zero.");
    }
}

inline std::size_t BufferedReader::buffered_bytes() const noexcept {
    return m_end - m_pos;
}

inline bool BufferedReader::refill() {
    m_pos = 0;
    m_end = 0;

    const auto read = m_upstream->read_some(m_buffer.data(), m_buffer.size());

    if (!read.has_value() || *read == 0) {
        return false;
    }

    m_end = *read;
    return true;
}

inline std::optional<std::size_t> BufferedReader::read_some(std::byte* data, std::size_t size) {
    if (size == 0) {
        return std::size_t{0};
    }

    if (m_pos < m_end) {
        const std::size_t avail = m_end - m_pos;
        const std::size_t to_copy = std::min(size, avail);

        for (std::size_t i = 0; i < to_copy; ++i) {
            data[i] = m_buffer[m_pos + i];
        }

        m_pos += to_copy;
        return to_copy;
    }

    if (size >= m_buffer.size()) {
        return m_upstream->read_some(data, size);
    }

    if (!refill()) {
        return std::nullopt;
    }

    const std::size_t to_copy = std::min(size, m_end - m_pos);

    for (std::size_t i = 0; i < to_copy; ++i) {
        data[i] = m_buffer[m_pos + i];
    }

    m_pos += to_copy;
    return to_copy;
}

inline std::string BufferedReader::read_until(char delimiter) {
    std::string result;
    const auto target = static_cast<std::byte>(delimiter);

    while (true) {
        if (m_pos == m_end && !refill()) {
            break;
        }

        while (m_pos < m_end) {
            const std::byte ch = m_buffer[m_pos++];
            result.push_back(static_cast<char>(ch));

            if (ch == target) {
                return result;
            }
        }
    }

    return result;
}

inline std::string BufferedReader::read_line() {
    std::string raw = read_until('\n');

    if (!raw.empty() && raw.back() == '\n') {
        raw.pop_back();
    }

    if (!raw.empty() && raw.back() == '\r') {
        raw.pop_back();
    }

    return raw;
}

} // namespace io