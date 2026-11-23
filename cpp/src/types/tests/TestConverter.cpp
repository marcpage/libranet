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

TEST(ConverterTest, ParseHex) {
    constexpr std::uint64_t two_digits = 0xa5;

    EXPECT_EQ(Converter::parse_hex(""), 0U);
    EXPECT_EQ(Converter::parse_hex("a5"), two_digits);
    EXPECT_EQ(Converter::parse_hex("ffffffffffffffff"), std::numeric_limits<std::uint64_t>::max());
}

TEST(ConverterTest, ParseHexRefusesMoreDigitsThanFit) {
    EXPECT_THROW((void)Converter::parse_hex("10000000000000000"), std::invalid_argument);
}
