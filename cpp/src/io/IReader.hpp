#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace io {

class IReader {
public:
    using Ptr = std::unique_ptr<IReader>;

    enum class EolAction : std::uint8_t {
        MustHaveEol,
        MayHaveEol,
    };

    // Reads a line up to its \n or the end of the input, and leaves out the \n and a \r before it
    [[nodiscard]] virtual std::optional<std::string> read_line(EolAction eolAction);
    // Reads size bytes, or fewer where the input ends first.
    [[nodiscard]] virtual std::vector<std::byte> read_exact(std::size_t size);
    // Reads up to and including the delimiter, or to the end of the input.
    [[nodiscard]] virtual std::string read_until(char delimiter);

    // Reads up to size bytes into data. Returns how many were read, or
    // nothing at the end of the input, before any are read.
    [[nodiscard]] virtual std::optional<std::size_t> read_some(std::byte* data,
                                                               std::size_t size) = 0;

    virtual ~IReader() = default;
    IReader(const IReader&) = delete;
    IReader& operator=(const IReader&) = delete;
    IReader(IReader&&) noexcept = default;
    IReader& operator=(IReader&&) noexcept = default;

protected:
    IReader() = default;
};

// Reads a piece at a time, so that a size larger than the input takes no
// more memory than the input has.
inline std::vector<std::byte> IReader::read_exact(std::size_t size) {
    // 64 KiB, as ISocket::read_exact() reads.
    constexpr std::size_t piece_size = 65536;
    std::vector<std::byte> result;
    std::vector<std::byte> piece;

    while (result.size() < size) {
        piece.resize(std::min(piece_size, size - result.size()));

        const auto read = read_some(piece.data(), piece.size());

        if (!read || 0 == *read) {
            break;
        }

        result.insert(result.end(), piece.begin(),
                      piece.begin() + static_cast<std::ptrdiff_t>(*read));
    }

    return result;
}

inline std::string IReader::read_until(char delimiter) {
    std::string result;
    const auto target = static_cast<std::byte>(delimiter);

    while (true) {
        std::byte single_byte{};
        const auto read = read_some(&single_byte, 1);

        if (!read || 0 == *read) {
            break;
        }

        result.push_back(static_cast<char>(single_byte));

        if (single_byte == target) {
            break;
        }
    }

    return result;
}

inline std::optional<std::string> IReader::read_line(EolAction eolAction) {
    std::string raw = read_until('\n');

    if (!raw.empty() && '\n' == raw.back()) {
        raw.pop_back();
    } else if (eolAction == EolAction::MayHaveEol) {
        return std::nullopt;
    }

    if (!raw.empty() && '\r' == raw.back()) {
        raw.pop_back();
    }

    return raw;
}

} // namespace io
