#pragma once

#include <cstddef>
#include <optional>
#include <vector>

namespace storage {

// Content as a zlib stream (RFC 1950), as it may be stored and sent
// (HttpApi §8), and back. Built on zlib.
class Zlib {
public:
    using Data = std::vector<std::byte>;

    // zlib's levels: 0 stores without compressing, 1 is fastest, and 9 makes
    // the smallest stream.
    static constexpr int min_level = 0;
    static constexpr int max_level = 9;

    // data as one zlib stream, compressed at level. Throws
    // std::invalid_argument for a level from outside min_level to max_level.
    [[nodiscard]] static Data compress(const Data& data, int level);

    // What the zlib stream data holds, or nothing when data is not exactly
    // one complete zlib stream, or when what it holds is more than limit
    // bytes. Decompressing stops just past limit, making room as it goes,
    // so a small stream that would expand without bound takes no more
    // memory than limit allows. Throws std::bad_alloc if zlib runs out of
    // memory.
    [[nodiscard]] static std::optional<Data> decompress(const Data& data, std::size_t limit);

    Zlib() = delete;
    ~Zlib() = delete;
    Zlib(const Zlib&) = delete;
    Zlib& operator=(const Zlib&) = delete;
    Zlib(Zlib&&) = delete;
    Zlib& operator=(Zlib&&) = delete;
};

} // namespace storage
