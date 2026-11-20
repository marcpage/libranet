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

#include "io/IReader.hpp"
#include "io/MeteredReader.hpp"

using io::IReader;
using io::LimitExceeded;
using io::MeteredReader;

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

// A reader of the text it is given, as much as fits each read.
class StringReader final : public IReader {
public:
    explicit StringReader(std::string data)
        : m_data(std::move(data)) {}

    [[nodiscard]] std::optional<std::size_t> read_some(std::byte* data,
                                                       std::size_t size) override {
        if (m_position >= m_data.size()) {
            return std::nullopt;
        }

        const auto count = std::min(size, m_data.size() - m_position);
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
    std::size_t m_position{0};
};

} // namespace

TEST(MeteredReaderTest, RejectsNullUpstream) {
    constexpr auto byte_limit = 10;

    // Made only to see its constructor throw.
    // cppcheck-suppress unusedScopedObject
    EXPECT_THROW(MeteredReader(nullptr, byte_limit), std::invalid_argument);
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
    EXPECT_EQ(as_text(reader.read_exact(new_byte_limit)), "body-payload");
}

// The end of the input, before the limit, is not counted against it.
TEST(MeteredReaderTest, EndOfInputBeforeTheLimitGivesNothing) {
    constexpr auto byte_limit = 6;
    MeteredReader reader(std::make_unique<StringReader>("ab"), byte_limit);
    std::array<std::byte, 4> data{};

    EXPECT_EQ(reader.read_some(data.data(), 0), 0U);
    EXPECT_EQ(reader.read_some(data.data(), data.size()), 2U);
    EXPECT_FALSE(reader.read_some(data.data(), 2).has_value());
    EXPECT_EQ(reader.bytes_read(), 2U);
}
