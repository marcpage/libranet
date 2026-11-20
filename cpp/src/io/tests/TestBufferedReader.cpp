#include <gtest/gtest.h>
#include <algorithm>
#include <array>
#include <cstddef>
#include <iterator>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "io/BufferedReader.hpp"
#include "io/IReader.hpp"

using io::BufferedReader;
using io::IReader;

namespace {

// The bytes as text, a char for each.
std::string as_text(const std::vector<std::byte>& bytes) {
    std::string text;

    text.reserve(bytes.size());
    std::transform(bytes.begin(), bytes.end(), std::back_inserter(text), [](std::byte byte) {
        return static_cast<char>(std::to_integer<unsigned char>(byte));
    });
    return text;
}

// A reader of the text it is given, at most max_chunk bytes a read, which
// counts its reads.
class StringReader final : public IReader {
public:
    static constexpr auto chunk_size = 64;

    explicit StringReader(std::string data, std::size_t max_chunk = chunk_size)
        : m_data(std::move(data))
        , m_max_chunk(max_chunk) {}

    [[nodiscard]] std::size_t reads() const noexcept {
        return m_reads;
    }

    [[nodiscard]] std::optional<std::size_t> read_some(std::byte* data,
                                                       std::size_t size) override {
        ++m_reads;

        if (m_position >= m_data.size()) {
            return std::nullopt;
        }

        const auto count = std::min({size, m_max_chunk, m_data.size() - m_position});
        const auto first = m_data.begin() + static_cast<std::string::difference_type>(m_position);

        std::transform(first, first + static_cast<std::string::difference_type>(count), data,
                       [](char single_byte) {
            return static_cast<std::byte>(single_byte);
        });
        m_position += count;
        return count;
    }

private:
    std::string m_data;
    std::size_t m_max_chunk;
    std::size_t m_position{0};
    std::size_t m_reads{0};
};

} // namespace

TEST(BufferedReaderTest, RejectsNullUpstreamOrZeroBufferSize) {
    constexpr auto buffer_size = 16;

    EXPECT_THROW(BufferedReader(nullptr, buffer_size), std::invalid_argument);
    EXPECT_THROW(BufferedReader(std::make_unique<StringReader>("abc"), 0), std::invalid_argument);
}

TEST(BufferedReaderTest, BuffersUpstreamReadsAndTracksBufferedBytes) {
    constexpr auto chunk_size = 64;
    constexpr auto buffer_size = 16;
    constexpr auto first_size = 5;
    constexpr auto second_size = 6;
    auto upstream = std::make_unique<StringReader>("hello world", chunk_size);
    const auto* raw_upstream = upstream.get();
    BufferedReader reader(std::move(upstream), buffer_size);

    EXPECT_EQ(as_text(reader.read_exact(first_size)), "hello");
    EXPECT_EQ(raw_upstream->reads(), 1U);
    EXPECT_EQ(reader.buffered_bytes(), 6U);
    EXPECT_EQ(as_text(reader.read_exact(second_size)), " world");
    EXPECT_EQ(reader.buffered_bytes(), 0U);
}

TEST(BufferedReaderTest, ReadUntilAcrossBufferRefills) {
    BufferedReader reader(std::make_unique<StringReader>("one|two|three", 4), 4);

    EXPECT_EQ(reader.read_until('|'), "one|");
    EXPECT_EQ(reader.read_until('|'), "two|");
    EXPECT_EQ(reader.read_until('|'), "three");
}

TEST(BufferedReaderTest, ReadLineHandlesCrLfAndEof) {
    constexpr auto chunk_size = 8;
    BufferedReader reader(std::make_unique<StringReader>("line1\r\nline2\nfinal", chunk_size),
                          chunk_size);

    EXPECT_EQ(reader.read_line(), "line1");
    EXPECT_EQ(reader.read_line(), "line2");
    EXPECT_EQ(reader.read_line(), "final");
    EXPECT_EQ(reader.read_line(), "");
}

// A read as large as the buffer, with nothing buffered, goes straight to the
// upstream reader, rather than through the buffer a piece at a time.
TEST(BufferedReaderTest, ReadAsLargeAsTheBufferIsNotBuffered) {
    constexpr auto buffer_size = 4;
    constexpr auto size = 6;
    auto upstream = std::make_unique<StringReader>("abcdefgh");
    const auto* raw_upstream = upstream.get();
    BufferedReader reader(std::move(upstream), buffer_size);

    EXPECT_EQ(as_text(reader.read_exact(size)), "abcdef");
    EXPECT_EQ(raw_upstream->reads(), 1U);
    EXPECT_EQ(reader.buffered_bytes(), 0U);
}

TEST(BufferedReaderTest, ReadAtTheEndGivesNothing) {
    constexpr auto buffer_size = 8;
    BufferedReader reader(std::make_unique<StringReader>("ab"), buffer_size);
    std::array<std::byte, 4> data{};

    EXPECT_EQ(reader.read_some(data.data(), 0), 0U);
    EXPECT_EQ(reader.read_some(data.data(), data.size()), 2U);
    EXPECT_FALSE(reader.read_some(data.data(), data.size()).has_value());
    EXPECT_TRUE(reader.read_exact(1).empty());
}
