#include <gtest/gtest.h>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>

#include "types/Converter.hpp"

using types::Converter;

TEST(ConverterTest, LowerCase) {
    EXPECT_EQ(Converter::lower_case("MiXeD 123"), "mixed 123");
}

TEST(ConverterTest, BytesOfTextReadBack) {
    const std::string text("a\0b", 3);

    EXPECT_EQ(Converter::bytes_of(text).size(), text.size());
    EXPECT_EQ(Converter::text_of(Converter::bytes_of(text)), text);
}

TEST(ConverterTest, IsUtf8) {
    EXPECT_TRUE(Converter::is_utf8(""));
    EXPECT_TRUE(Converter::is_utf8("plain ASCII"));
    EXPECT_TRUE(Converter::is_utf8(std::string("a\0b", 3)));
    // U+00E9, U+20AC, U+1F511, and the last one there is, U+10FFFF.
    EXPECT_TRUE(Converter::is_utf8("\xC3\xA9\xE2\x82\xAC\xF0\x9F\x94\x91\xF4\x8F\xBF\xBF"));
    // U+0080, U+0800, U+D7FF, U+E000, and U+10000, each the first or last
    // a length or range takes.
    EXPECT_TRUE(Converter::is_utf8("\xC2\x80\xE0\xA0\x80\xED\x9F\xBF\xEE\x80\x80\xF0\x90\x80\x80"));
}

TEST(ConverterTest, IsUtf8RefusesWhatIsNot) {
    // A byte no character starts with.
    EXPECT_FALSE(Converter::is_utf8("\xFF"));
    EXPECT_FALSE(Converter::is_utf8("\x80"));
    // A '/' written in two bytes, where it takes one.
    EXPECT_FALSE(Converter::is_utf8("\xC0\xAF"));
    // U+07FF written in three bytes, where it takes two.
    EXPECT_FALSE(Converter::is_utf8("\xE0\x9F\xBF"));
    // U+FFFF written in four bytes, where it takes three.
    EXPECT_FALSE(Converter::is_utf8("\xF0\x8F\xBF\xBF"));
    // A surrogate, U+D800.
    EXPECT_FALSE(Converter::is_utf8("\xED\xA0\x80"));
    // One past U+10FFFF.
    EXPECT_FALSE(Converter::is_utf8("\xF4\x90\x80\x80"));
    // A character cut short, at the end and before more text.
    EXPECT_FALSE(Converter::is_utf8("ok\xE2\x82"));
    EXPECT_FALSE(Converter::is_utf8("\xE2\x82z"));
    // A byte after the second that is not one a character goes on with.
    EXPECT_FALSE(Converter::is_utf8("\xF0\x9F\x94\x41"));
}

TEST(ConverterTest, ParseHex) {
    constexpr std::uint64_t two_digits = 0xA5;

    EXPECT_EQ(Converter::parse_hex(""), 0U);
    EXPECT_EQ(Converter::parse_hex("a5"), two_digits);
    EXPECT_EQ(Converter::parse_hex("ffffffffffffffff"), std::numeric_limits<std::uint64_t>::max());
}

TEST(ConverterTest, ParseHexRefusesMoreDigitsThanFit) {
    EXPECT_THROW((void)Converter::parse_hex("10000000000000000"), std::invalid_argument);
}
