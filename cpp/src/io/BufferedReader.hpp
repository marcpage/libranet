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

    [[nodiscard]] std::string read_until(char delimiter) override;
    [[nodiscard]] std::string read_line() override;
    [[nodiscard]] std::size_t buffered_bytes() const noexcept;

    std::optional<std::size_t> read_some(std::byte* data, std::size_t size) override;

private:
    bool _refill();

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

inline bool BufferedReader::_refill() {
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
        const std::size_t to_copy = std::min(size, m_end - m_pos);
        const auto first = m_buffer.begin() + static_cast<std::vector<std::byte>::difference_type>(m_pos);

        std::copy_n(first, to_copy, data);
        m_pos += to_copy;
        return to_copy;
    }

    if (size >= m_buffer.size()) {
        return m_upstream->read_some(data, size);
    }

    if (!_refill()) {
        return std::nullopt;
    }

    const std::size_t to_copy = std::min(size, m_end - m_pos);
    const auto first = m_buffer.begin() + static_cast<std::vector<std::byte>::difference_type>(m_pos);

    std::copy_n(first, to_copy, data);
    m_pos += to_copy;
    return to_copy;
}

inline std::string BufferedReader::read_until(char delimiter) {
    std::string result;
    const auto target = static_cast<std::byte>(delimiter);

    while (true) {
        if (m_pos == m_end && !_refill()) {
            break;
        }

        const auto begin = m_buffer.begin();
        const auto start = begin + static_cast<std::vector<std::byte>::difference_type>(m_pos);
        const auto end = begin + static_cast<std::vector<std::byte>::difference_type>(m_end);
        const auto found = std::find(start, end, target);

        if (found != end) {
            const auto inclusive_end = std::next(found);

            std::transform(start, inclusive_end, std::back_inserter(result), [](std::byte byte) {
                return static_cast<char>(std::to_integer<unsigned char>(byte));
            });
            m_pos += static_cast<std::size_t>(std::distance(start, inclusive_end));
            return result;
        }

        std::transform(start, end, std::back_inserter(result), [](std::byte byte) {
            return static_cast<char>(std::to_integer<unsigned char>(byte));
        });
        m_pos = m_end;
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