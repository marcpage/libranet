// libFuzzer calls this with inputs it makes up. Zlib::decompress() turns
// down an input that is not one complete zlib stream, or holds more than its
// limit, with an empty optional; anything else it does with an input, such
// as crash, hang, or throw, is a fault. What it reads, compressed again, it
// must read back as the same content.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <iterator>

#include "storage/Zlib.hpp"
#include "types/Constants.hpp"

using storage::Zlib;

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    // Far more than what the fuzzer's inputs, of a few KiB, hold, but little
    // enough that a stream that expands far does not take its memory.
    constexpr std::size_t limit = 1 * types::Megabyte;
    Zlib::Data input(size);
    std::transform(data, std::next(data, static_cast<std::ptrdiff_t>(size)), input.begin(),
                   [](std::uint8_t byte) { return static_cast<std::byte>(byte); });

    const auto content = Zlib::decompress(input, limit);

    if (!content) {
        return 0;
    }

    if (Zlib::decompress(Zlib::compress(*content, Zlib::max_level), limit) != content) {
        std::cerr << "Zlib did not read back what it compressed, of " << content->size()
                  << " bytes\n";
        std::abort();
    }

    return 0;
}
