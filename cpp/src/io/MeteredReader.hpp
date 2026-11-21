#pragma once

#include <cstddef>
#include <optional>
#include <stdexcept>
#include <utility>

#include "IReader.hpp"

namespace io {

class LimitExceeded : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// Reads from another reader no more than a limit, so that input from
// outside takes no more than it is allowed. A read asking for more than
// remains throws LimitExceeded before reading anything, so one goes over a
// BufferedReader rather than under it, where each read asks for a buffer's
// worth. reset() gives a new limit and starts the count again, as for each
// part of a request.
class MeteredReader final : public IReader {
public:
    MeteredReader(IReader::Ptr upstream, std::size_t max_bytes);

    void reset(std::size_t max_bytes) noexcept;
    [[nodiscard]] std::size_t bytes_read() const noexcept;
    [[nodiscard]] std::size_t remaining() const noexcept;

    [[nodiscard]] std::optional<std::size_t> read_some(std::byte* data, std::size_t size) override;

private:
    IReader::Ptr m_upstream;
    std::size_t m_max_bytes;
    std::size_t m_bytes_read{0};
};

inline MeteredReader::MeteredReader(IReader::Ptr upstream, std::size_t max_bytes)
    : m_upstream(std::move(upstream))
    , m_max_bytes(max_bytes) {
    if (!m_upstream) {
        throw std::invalid_argument("MeteredReader requires a non-null upstream reader");
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
    if (0 == size) {
        return std::size_t{0};
    }

    if (size > remaining()) {
        throw LimitExceeded("Read exceeds configured byte limit");
    }

    const auto read = m_upstream->read_some(data, size);

    if (read) {
        m_bytes_read += *read;
    }

    return read;
}

} // namespace io
