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

#include "io/IReader.hpp"
#include "io/MeteredReader.hpp"

using io::IReader;
using io::LimitExceeded;
using io::MeteredReader;

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
    explicit StringReader(std::string data)
        : m_data(std::move(data)) {}

    std::optional<std::size_t> read_some(std::byte* data, std::size_t size) override {
        if (m_pos >= m_data.size()) {
            return std::nullopt;
        }

        const auto count = std::min(size, m_data.size() - m_pos);
        const auto first = m_data.begin() + static_cast<std::string::difference_type>(m_pos);

        std::transform(first, first + static_cast<std::string::difference_type>(count), data,
                       [](char single_byte) {
            return static_cast<std::byte>(single_byte);
        });
        m_pos += count;
        return count;
    }

private:
    std::string m_data;
    std::size_t m_pos{0};
};

} // namespace

TEST(MeteredReaderTest, RejectsNullUpstream) {
    EXPECT_THROW(MeteredReader(nullptr, 10), std::invalid_argument);
}

TEST(MeteredReaderTest, TracksBytesReadAndRemaining) {
    constexpr auto byte_limit = 6;
    MeteredReader reader(std::make_unique<StringReader>("abcdef"), byte_limit);

    EXPECT_EQ(reader.bytes_read(), 0U);
    EXPECT_EQ(reader.remaining(), 6U);

    EXPECT_EQ(as_text(reader.read_exact(4)), "abcd");
    EXPECT_EQ(reader.bytes_read(), 4U);
    EXPECT_EQ(reader.remaining(), 2U);
}

TEST(MeteredReaderTest, ThrowsLimitExceededWhenLimitIsCrossed) {
    constexpr auto byte_limit = 5;
    MeteredReader reader(std::make_unique<StringReader>("abcdefghij"), byte_limit);

    EXPECT_EQ(as_text(reader.read_exact(3)), "abc");
    EXPECT_THROW((void)reader.read_line(), LimitExceeded);
}

TEST(MeteredReaderTest, ResetRestoresBudgetAndClearsCount) {
    constexpr auto byte_limit = 6;
    constexpr auto new_byte_limit = 12;
    MeteredReader reader(std::make_unique<StringReader>("head\r\nbody-payload"), byte_limit);

    EXPECT_EQ(reader.read_line(), "head");
    EXPECT_EQ(reader.bytes_read(), 6U);
    EXPECT_EQ(reader.remaining(), 0U);

    reader.reset(new_byte_limit);
    EXPECT_EQ(reader.bytes_read(), 0U);
    EXPECT_EQ(reader.remaining(), 12U);
    EXPECT_EQ(as_text(reader.read_exact(12)), "body-payload");
}