#include <gtest/gtest.h>
#include <cstddef>
#include <sstream>
#include <stdexcept>
#include <string>

#include "types/Integer.hpp"

using types::Integer;
using types::operator""_i;

TEST(IntegerTest, ConstructorsAndExceptions) {
    // Default constructor (zero state)
    const Integer a;
    EXPECT_TRUE(a.is_zero());
    EXPECT_EQ(a.to_string(), "0");
    EXPECT_FALSE(a.is_negative());
    EXPECT_EQ(a.sign(), 0);
    EXPECT_FALSE(static_cast<bool>(a));

    // Numeric constructors
    constexpr auto positive_value = 42LL;
    constexpr auto negative_value = -42LL;
    const Integer b(positive_value);
    EXPECT_EQ(b.to_string(), "42");
    EXPECT_EQ(b.sign(), 1);

    const Integer c(negative_value);
    EXPECT_EQ(c.to_string(), "-42");
    EXPECT_TRUE(c.is_negative());
    EXPECT_EQ(c.sign(), -1);

    // String parsing & normalization
    const Integer d("+000123");
    EXPECT_EQ(d.to_string(), "123");

    const Integer e("-0");
    EXPECT_TRUE(e.is_zero()); // Zero should never be negative

    // Invalid string exceptions
    EXPECT_THROW((void)Integer(""), std::invalid_argument);
    EXPECT_THROW((void)Integer("123a45"), std::invalid_argument);
}

TEST(IntegerTest, UnaryAndIncrement) {
    constexpr auto start = 10;
    Integer x(start);

    // Unary plus and minus[cite: 1]
    EXPECT_EQ((+x).to_string(), "10");
    EXPECT_EQ((-x).to_string(), "-10");

    // Increment operators[cite: 1]
    const Integer pre_inc = ++x;
    EXPECT_EQ(pre_inc.to_string(), "11");
    EXPECT_EQ(x.to_string(), "11");

    const Integer post_inc = x++;
    EXPECT_EQ(post_inc.to_string(), "11");
    EXPECT_EQ(x.to_string(), "12");

    // Decrement operators[cite: 1]
    const Integer pre_dec = --x;
    EXPECT_EQ(pre_dec.to_string(), "11");
    EXPECT_EQ(x.to_string(), "11");

    const Integer post_dec = x--;
    EXPECT_EQ(post_dec.to_string(), "11");
    EXPECT_EQ(x.to_string(), "10");

    // Bitwise NOT (~x == -x - 1)[cite: 1]
    const Integer not_val = ~Integer(0);
    EXPECT_EQ(not_val.to_string(), "-1");
}

TEST(IntegerTest, ArithmeticAndDivisions) {
    // Compound assignments[cite: 1]
    constexpr auto start = 100;
    constexpr auto addend = 50;
    constexpr auto subtrahend = 30;
    constexpr auto modulus = 40;
    Integer val(start);
    val += Integer(addend);
    EXPECT_EQ(val.to_string(), "150");

    val -= Integer(subtrahend);
    EXPECT_EQ(val.to_string(), "120");

    val *= Integer(3);
    EXPECT_EQ(val.to_string(), "360");

    val /= Integer(4);
    EXPECT_EQ(val.to_string(), "90");

    val %= Integer(modulus);
    EXPECT_EQ(val.to_string(), "10");

    // Division by zero exception[cite: 1]
    constexpr auto dividend = 5;
    EXPECT_THROW((void)(Integer(dividend) / Integer(0)), std::domain_error);
    EXPECT_THROW((void)(Integer(dividend) % Integer(0)), std::domain_error);
}

TEST(IntegerTest, BitwiseAndShifts) {
    constexpr auto left_value = 12;  // 1100 in binary
    constexpr auto right_value = 10; // 1010 in binary
    const Integer a(left_value);
    const Integer b(right_value);

    EXPECT_EQ((a & b).to_string(), "8");  // 1000
    EXPECT_EQ((a | b).to_string(), "14"); // 1110
    EXPECT_EQ((a ^ b).to_string(), "6");  // 0110

    // Shifts[cite: 1]
    constexpr std::size_t shift_bits = 64; // Test cross-word shift boundary
    Integer shift_val(1);
    shift_val <<= shift_bits;
    EXPECT_EQ(shift_val.bit_length(), 65U);

    shift_val >>= shift_bits;
    EXPECT_EQ(shift_val.to_string(), "1");
}

TEST(IntegerTest, StreamsAndLiterals) {
    // Stream Output[cite: 1]
    constexpr auto output_value = 987654321987654321LL;
    const Integer test_out(output_value);
    std::ostringstream oss;
    oss << test_out;
    EXPECT_EQ(oss.str(), "987654321987654321");

    // Stream Input[cite: 1]
    std::istringstream iss("12345678901234567890");
    Integer test_in;
    iss >> test_in;
    EXPECT_EQ(test_in.to_string(), "12345678901234567890");

    // Failed stream input check[cite: 1]
    std::istringstream bad_iss("invalid_number");
    bad_iss >> test_in;
    EXPECT_TRUE(bad_iss.fail());

    // User-defined literal[cite: 1]
    const Integer lit = "999888777666555444"_i;
    EXPECT_EQ(lit.to_string(), "999888777666555444");
}

TEST(IntegerTest, LargeScaleStress) {
    // Compute a pseudo-factorial or large growth
    constexpr int last_factor = 30;
    Integer accumulator(1);
    for (int i = 1; i <= last_factor; ++i) {
        accumulator *= Integer(i);
    }
    // 30! = 265252859812191058636308480000000
    EXPECT_EQ(accumulator.to_string(), "265252859812191058636308480000000");
    EXPECT_GT(accumulator.bit_length(), 100U);
}
