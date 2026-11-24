#pragma once

#include <cstddef>
#include <optional>
#include <vector>

namespace storage {

// Content as a zlib stream (RFC 1950), as it may be stored and sent (HttpApi
// §8), and back.
class Zlib {
public:
    using Data = std::vector<std::byte>;

    // 0 stores without compressing, 1 is fastest, and 9 makes the smallest.
    static constexpr int min_level = 0;
    static constexpr int balance_level = 6;
    static constexpr int max_level = 9;

    // Throws std::invalid_argument for a level outside min_level to max_level.
    [[nodiscard]] static Data compress(const Data& data, int level);

    // Nothing when data is not exactly one zlib stream, or holds more than
    // limit bytes, which bounds the memory it takes. Throws std::bad_alloc if
    // zlib runs out of memory.
    [[nodiscard]] static std::optional<Data> decompress(const Data& data, std::size_t limit);

    Zlib() = delete;
    ~Zlib() = delete;
    Zlib(const Zlib&) = delete;
    Zlib& operator=(const Zlib&) = delete;
    Zlib(Zlib&&) = delete;
    Zlib& operator=(Zlib&&) = delete;
};

} // namespace storage
