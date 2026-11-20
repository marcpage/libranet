#include <gtest/gtest.h>
#include <algorithm>
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

std::string as_text(const std::vector<std::byte>& bytes) {
    std::string text;

    text.reserve(bytes.size());
    std::transform(bytes.begin(), bytes.end(), std::back_inserter(text), [](std::byte byte) {
        return static_cast<char>(std::to_integer<unsigned char>(byte));
    });
    return text;
}

class StringReader final : public IReader {
public:
    explicit StringReader(std::string data, std::size_t max_chunk = 64)
        : m_data(std::move(data))
        , m_max_chunk(max_chunk) {}

    [[nodiscard]] std::size_t reads() const noexcept {
        return m_reads;
    }

protected:
    std::optional<std::size_t> read_some(std::byte* data, std::size_t size) override {
        ++m_reads;

        if (m_pos >= m_data.size()) {
            return std::nullopt;
        }

        const auto count = std::min({size, m_max_chunk, m_data.size() - m_pos});
        const auto first = m_data.begin() + static_cast<std::string::difference_type>(m_pos);

        std::transform(first, first + static_cast<std::string::difference_type>(count), data,
                       [](char ch) {
            return static_cast<std::byte>(ch);
        });
        m_pos += count;
        return count;
    }

private:
    std::string m_data;
    std::size_t m_max_chunk;
    std::size_t m_pos{0};
    std::size_t m_reads{0};
};

} // namespace

TEST(BufferedReaderTest, RejectsNullUpstreamOrZeroBufferSize) {
    EXPECT_THROW(BufferedReader(nullptr, 16), std::invalid_argument);
    EXPECT_THROW(BufferedReader(std::make_unique<StringReader>("abc"), 0), std::invalid_argument);
}

TEST(BufferedReaderTest, BuffersUpstreamReadsAndTracksBufferedBytes) {
    auto upstream = std::make_unique<StringReader>("hello world", 64);
    const auto* raw_upstream = upstream.get();
    BufferedReader reader(std::move(upstream), 16);

    EXPECT_EQ(as_text(reader.read_exact(5)), "hello");
    EXPECT_EQ(raw_upstream->reads(), 1U);
    EXPECT_EQ(reader.buffered_bytes(), 6U);
    EXPECT_EQ(as_text(reader.read_exact(6)), " world");
    EXPECT_EQ(reader.buffered_bytes(), 0U);
}

TEST(BufferedReaderTest, ReadUntilAcrossBufferRefills) {
    BufferedReader reader(std::make_unique<StringReader>("one|two|three", 4), 4);

    EXPECT_EQ(reader.read_until('|'), "one|");
    EXPECT_EQ(reader.read_until('|'), "two|");
    EXPECT_EQ(reader.read_until('|'), "three");
}

TEST(BufferedReaderTest, ReadLineHandlesCrLfAndEof) {
    BufferedReader reader(std::make_unique<StringReader>("line1\r\nline2\nfinal", 8), 8);

    EXPECT_EQ(reader.read_line(), "line1");
    EXPECT_EQ(reader.read_line(), "line2");
    EXPECT_EQ(reader.read_line(), "final");
    EXPECT_EQ(reader.read_line(), "");
}