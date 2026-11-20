#pragma once

#include <cstddef>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace io {

class IReader {
public:
    using Ptr = std::unique_ptr<IReader>;

    virtual ~IReader() = default;

    // Reads up to `size` bytes, stopping early only on EOF.
    [[nodiscard]] std::vector<std::byte> read_exact(std::size_t size);

    // Reads up to and including `delimiter`, or until EOF.
    [[nodiscard]] std::string read_until(char delimiter);

    // Reads a line ending at '\n' (or EOF), stripping '\r' and '\n'.
    [[nodiscard]] std::string read_line();

    IReader(const IReader&) = delete;
    IReader& operator=(const IReader&) = delete;
    IReader(IReader&&) = delete;
    IReader& operator=(IReader&&) = delete;

    // Reads up to `size` bytes into `data`.
    // Returns bytes read, or std::nullopt on EOF before any bytes are read.
    virtual std::optional<std::size_t> read_some(std::byte* data, std::size_t size) = 0;

protected:
    IReader() = default;
};

inline std::vector<std::byte> IReader::read_exact(std::size_t size) {
    std::vector<std::byte> result;
    result.reserve(size);

    while (result.size() < size) {
        std::vector<std::byte> chunk(size - result.size());
        const auto read = read_some(chunk.data(), chunk.size());

        if (!read.has_value() || *read == 0) {
            break;
        }

        result.insert(result.end(), chunk.begin(),
                      chunk.begin() + static_cast<std::ptrdiff_t>(*read));
    }

    return result;
}

inline std::string IReader::read_until(char delimiter) {
    std::string result;
    const auto target = static_cast<std::byte>(delimiter);

    while (true) {
        std::byte single_byte{};
        const auto read = read_some(&single_byte, 1);

        if (!read.has_value() || *read == 0) {
            break;
        }

        result.push_back(static_cast<char>(single_byte));

        if (single_byte == target) {
            break;
        }
    }

    return result;
}

inline std::string IReader::read_line() {
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