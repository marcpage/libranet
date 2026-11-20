#pragma once

#include <cstddef>
#include <optional>
#include <stdexcept>
#include <utility>

#include "io/IReader.hpp"

namespace io {

class LimitExceeded : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class MeteredReader final : public IReader {
public:
    MeteredReader(IReader::Ptr upstream, std::size_t max_bytes);

    void reset(std::size_t max_bytes) noexcept;
    [[nodiscard]] std::size_t bytes_read() const noexcept;
    [[nodiscard]] std::size_t remaining() const noexcept;

    std::optional<std::size_t> read_some(std::byte* data, std::size_t size) override;

private:
    IReader::Ptr m_upstream;
    std::size_t m_max_bytes{0};
    std::size_t m_bytes_read{0};
};

inline MeteredReader::MeteredReader(IReader::Ptr upstream, std::size_t max_bytes)
    : m_upstream(std::move(upstream))
    , m_max_bytes(max_bytes) {
    if (!m_upstream) {
        throw std::invalid_argument("MeteredReader requires a non-null upstream reader.");
    }
}

inline void MeteredReader::reset(std::size_t max_bytes) noexcept {
    m_max_bytes = max_bytes;
    m_bytes_read = 0;
}

inline std::size_t MeteredReader::bytes_read() const noexcept {
    return m_bytes_read;
}

inline std::size_t MeteredReader::remaining() const noexcept {
    if (m_bytes_read >= m_max_bytes) {
        return 0;
    }

    return m_max_bytes - m_bytes_read;
}

inline std::optional<std::size_t> MeteredReader::read_some(std::byte* data, std::size_t size) {
    if (size == 0) {
        return std::size_t{0};
    }

    if (size > remaining()) {
        throw LimitExceeded("Read exceeds configured byte limit.");
    }

    const auto read = m_upstream->read_some(data, size);

    if (!read.has_value()) {
        return std::nullopt;
    }

    m_bytes_read += *read;
    return read;
}

} // namespace io