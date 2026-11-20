#include "Zlib.hpp"

#include <algorithm>
#include <cstddef>
#include <limits>
#include <new>
#include <optional>
#include <stdexcept>
#include <string>

#include <zconf.h>
#include <zlib.h>

namespace {

using Data = storage::Zlib::Data;

// How much more room is made at a time for what a stream holds, as it is
// decompressed: 64 KiB, as the Python node decompresses a piece at a time,
// so that a stream that holds little makes little room.
constexpr std::size_t room_step = 64UL * 1024UL;

// The most bytes zlib reads or writes in one call: it counts them in an
// unsigned int.
constexpr std::size_t most_per_call = std::numeric_limits<uInt>::max();

// Where zlib is to read or write data, from offset, which may be its end.
// zlib takes bytes as Bytef, an unsigned char, which a std::byte may be read
// as. The return types are deduced: CodeQL reads the code without building
// it, and may take a declaration that starts with a type of zlib's for a
// variable, as it did one that started with a type of yaml-cpp's.
// NOLINTBEGIN(cppcoreguidelines-pro-type-reinterpret-cast)
// NOLINTBEGIN(cppcoreguidelines-pro-bounds-pointer-arithmetic)
auto* bytes_at(const Data& data, std::size_t offset) {
    return reinterpret_cast<const Bytef*>(data.data()) + offset;
}

auto* bytes_at(Data& data, std::size_t offset) {
    return reinterpret_cast<Bytef*>(data.data()) + offset;
}
// NOLINTEND(cppcoreguidelines-pro-bounds-pointer-arithmetic)
// NOLINTEND(cppcoreguidelines-pro-type-reinterpret-cast)

// As many of count bytes as zlib takes in one call.
auto per_call(std::size_t count) {
    return static_cast<uInt>(std::min(count, most_per_call));
}

// The size to make what a stream holds once it has filled size: a step
// more, but no more than one byte past limit, which only a stream holding
// more than limit fills.
std::size_t next_size(std::size_t size, std::size_t limit) {
    if (size >= limit) {
        return size + 1;
    }

    return size + std::min(room_step, limit - size);
}

// Throws for a result of zlib's that the data is not to blame for:
// std::bad_alloc when zlib ran out of memory, and std::runtime_error when it
// was not the zlib its header describes.
[[noreturn]] void throw_failure(int result) {
    if (Z_MEM_ERROR == result) {
        throw std::bad_alloc();
    }

    throw std::runtime_error(std::string("A zlib call failed: ") + zError(result));
}

// A zlib stream being inflated, ended when this goes out of scope. Each call
// is given the same input, and output that may have grown, and this keeps
// how far it has read and written them. zlib keeps a pointer to the
// z_stream, so this is neither copied nor moved.
class Inflater {
public:
    Inflater();
    ~Inflater();
    Inflater(const Inflater&) = delete;
    Inflater& operator=(const Inflater&) = delete;
    Inflater(Inflater&&) = delete;
    Inflater& operator=(Inflater&&) = delete;

    // inflate()'s result, once it has read what it can of input after what
    // it read before, and written what it can to output after what it wrote
    // before, up to output's end.
    int inflate_some(const Data& input, Data& output);

    [[nodiscard]] std::size_t read() const noexcept;
    [[nodiscard]] std::size_t written() const noexcept;

private:
    z_stream m_stream{};
    std::size_t m_read = 0;
    std::size_t m_written = 0;
};

Inflater::Inflater() {
    const auto result = inflateInit(&m_stream);

    if (Z_OK != result) {
        throw_failure(result);
    }
}

Inflater::~Inflater() {
    (void)inflateEnd(&m_stream);
}

int Inflater::inflate_some(const Data& input, Data& output) {
    const auto input_count = per_call(input.size() - m_read);
    const auto output_count = per_call(output.size() - m_written);

    m_stream.next_in = bytes_at(input, m_read);
    m_stream.avail_in = input_count;
    m_stream.next_out = bytes_at(output, m_written);
    m_stream.avail_out = output_count;

    const auto result = inflate(&m_stream, Z_NO_FLUSH);

    m_read += input_count - m_stream.avail_in;
    m_written += output_count - m_stream.avail_out;
    return result;
}

std::size_t Inflater::read() const noexcept {
    return m_read;
}

std::size_t Inflater::written() const noexcept {
    return m_written;
}

} // namespace

namespace storage {

Zlib::Data Zlib::compress(const Data& data, int level) {
    if (level < min_level || max_level < level) {
        throw std::invalid_argument("A zlib level must be from 0 to 9: " + std::to_string(level));
    }

    auto size = compressBound(data.size());
    Data compressed(size);
    const auto result =
        compress2(bytes_at(compressed, 0), &size, bytes_at(data, 0), data.size(), level);

    if (Z_OK != result) {
        throw_failure(result);
    }

    compressed.resize(size);
    return compressed;
}

std::optional<Zlib::Data> Zlib::decompress(const Data& data, std::size_t limit) {
    Inflater inflater;
    Data content;
    auto result = Z_OK;

    while (Z_OK == result && inflater.written() <= limit) {
        if (inflater.written() >= content.size()) {
            content.resize(next_size(content.size(), limit));
        }

        result = inflater.inflate_some(data, content);
    }

    if (Z_MEM_ERROR == result) {
        throw_failure(result);
    }

    // Anything but the end of the stream is a stream that holds more than
    // limit, is cut short, is not one, or needs a dictionary it was not
    // given; and a stream is all of data, with nothing after it.
    if (Z_STREAM_END != result || inflater.written() > limit || inflater.read() < data.size()) {
        return std::nullopt;
    }

    content.resize(inflater.written());
    return content;
}

} // namespace storage
