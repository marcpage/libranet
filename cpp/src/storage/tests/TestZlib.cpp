#include <gtest/gtest.h>
#include <cstddef>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "storage/Zlib.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"

using storage::Zlib;
using types::Converter;
using types::Megabyte;

namespace {

// "hello" as a zlib stream, as Python's zlib.compress(b"hello", 9) writes
// it, and at level 0, stored without compressing.
constexpr auto hello_level_9 = "78dacb48cdc9c90700062c0215";
constexpr auto hello_level_0 = "7801010500faff68656c6c6f062c0215";

// "hello" as a gzip stream, as Python's gzip.compress(b"hello", mtime=0)
// writes it: deflated as a zlib stream is, but with another header and
// check.
constexpr auto hello_gzip = "1f8b08000000000002ffcb48cdc9c9070086a6103605000000";

// Enough for a test's content to decompress to.
constexpr std::size_t enough = 1'024;

// The bytes that lowercase hex names.
Zlib::Data bytes(std::string_view text) {
    constexpr std::string_view digits = "0123456789abcdef";
    constexpr auto nibble_bits = 4U;
    Zlib::Data data;

    for (std::size_t index = 0; index + 1 < text.size(); index += 2) {
        const auto high = digits.find(text.at(index));
        const auto low = digits.find(text.at(index + 1));

        data.push_back(static_cast<std::byte>((high << nibble_bits) | low));
    }

    return data;
}

// Content of size bytes that repeats, so that it compresses well.
Zlib::Data repeating(std::size_t size) {
    constexpr std::string_view text = "Libranet stores content as it is, or compressed. ";
    Zlib::Data data;

    data.reserve(size);

    for (std::size_t index = 0; index < size; ++index) {
        data.push_back(static_cast<std::byte>(text.at(index % text.size())));
    }

    return data;
}

// Whether Zlib::decompress() turns down data, with limit.
bool refused(const Zlib::Data& data, std::size_t limit = enough) {
    return !Zlib::decompress(data, limit).has_value();
}

} // namespace

TEST(ZlibTest, DecompressesWhatPythonCompressed) {
    EXPECT_EQ(Zlib::decompress(bytes(hello_level_9), enough), Converter::bytes_of("hello"));
    EXPECT_EQ(Zlib::decompress(bytes(hello_level_0), enough), Converter::bytes_of("hello"));
}

TEST(ZlibTest, CompressesAsPythonDoes) {
    EXPECT_EQ(Zlib::compress(Converter::bytes_of("hello"), Zlib::max_level), bytes(hello_level_9));
    EXPECT_EQ(Zlib::compress(Converter::bytes_of("hello"), Zlib::min_level), bytes(hello_level_0));
}

TEST(ZlibTest, EachLevelReadsBack) {
    const auto content = repeating(enough);

    for (auto level = Zlib::min_level; level <= Zlib::max_level; ++level) {
        EXPECT_EQ(Zlib::decompress(Zlib::compress(content, level), enough), content)
            << "level " << level;
    }
}

TEST(ZlibTest, NothingReadsBack) {
    const Zlib::Data nothing;

    EXPECT_EQ(Zlib::decompress(Zlib::compress(nothing, Zlib::max_level), 0), nothing);
}

TEST(ZlibTest, RepeatedContentCompresses) {
    const auto content = repeating(enough);

    EXPECT_LT(Zlib::compress(content, Zlib::max_level).size(), content.size() / 4);
}

TEST(ZlibTest, LevelOutsideZlibsIsRefused) {
    const auto content = Converter::bytes_of("hello");

    EXPECT_THROW((void)Zlib::compress(content, Zlib::min_level - 1), std::invalid_argument);
    EXPECT_THROW((void)Zlib::compress(content, Zlib::max_level + 1), std::invalid_argument);
}

// Sizes on either side of the steps in which room is made as a stream is
// decompressed, which are 64 KiB.
TEST(ZlibTest, ContentUpToTheLimitReadsBack) {
    const std::vector<std::size_t> sizes{1, 65'535, 65'536, 65'537, 131'072, 200'000};

    for (const auto size : sizes) {
        const auto content = repeating(size);
        const auto compressed = Zlib::compress(content, Zlib::max_level);

        EXPECT_EQ(Zlib::decompress(compressed, size), content) << size << " bytes";
        EXPECT_TRUE(refused(compressed, size - 1)) << size << " bytes";
    }
}

TEST(ZlibTest, NoLimitReadsBack) {
    const auto content = repeating(enough);
    const auto compressed = Zlib::compress(content, Zlib::max_level);

    EXPECT_EQ(Zlib::decompress(compressed, std::numeric_limits<std::size_t>::max()), content);
}

// A small stream that expands to far more than its limit is stopped there,
// rather than expanded whole.
TEST(ZlibTest, StreamThatExpandsPastTheLimitIsRefused) {
    constexpr std::size_t size = 16 * Megabyte;
    constexpr std::size_t limit = 1 * Megabyte;
    const Zlib::Data zeros(size);
    const auto compressed = Zlib::compress(zeros, Zlib::max_level);

    ASSERT_LT(compressed.size(), limit / 4);
    EXPECT_TRUE(refused(compressed, limit));
}

TEST(ZlibTest, WhatIsNotAZlibStreamIsRefused) {
    EXPECT_TRUE(refused(Zlib::Data{}));
    EXPECT_TRUE(refused(Converter::bytes_of("hello")));
    EXPECT_TRUE(refused(bytes(hello_gzip)));
}

TEST(ZlibTest, StreamCutShortIsRefused) {
    auto compressed = bytes(hello_level_9);

    compressed.pop_back();
    EXPECT_TRUE(refused(compressed));

    compressed.resize(2);
    EXPECT_TRUE(refused(compressed));
}

TEST(ZlibTest, BytesAfterTheStreamAreRefused) {
    auto compressed = bytes(hello_level_9);
    const auto one_stream = compressed;

    compressed.push_back(std::byte{0});
    EXPECT_TRUE(refused(compressed));

    compressed.pop_back();
    compressed.insert(compressed.end(), one_stream.begin(), one_stream.end());
    EXPECT_TRUE(refused(compressed));
}

// The stream ends with an Adler-32 of what it holds.
TEST(ZlibTest, StreamWhoseCheckDoesNotMatchIsRefused) {
    auto compressed = bytes(hello_level_9);

    compressed.back() ^= std::byte{1};
    EXPECT_TRUE(refused(compressed));
}
