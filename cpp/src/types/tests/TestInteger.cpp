#include <gtest/gtest.h>
#include <cstddef>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "types/Integer.hpp"

using types::Integer;
using types::operator""_i;

namespace {

// first & second, first | second, and first ^ second, as "and or xor".
std::string bitwise_results(const Integer& first, const Integer& second) {
    return (first & second).to_string() + " " + (first | second).to_string() + " "
           + (first ^ second).to_string();
}

// As bitwise_results, from &=, |=, and ^=.
std::string assigned_bitwise_results(const Integer& first, const Integer& second) {
    Integer and_result = first;
    Integer or_result = first;
    Integer xor_result = first;

    and_result &= second;
    or_result |= second;
    xor_result ^= second;
    return and_result.to_string() + " " + or_result.to_string() + " " + xor_result.to_string();
}

} // namespace

TEST(IntegerTest, ConstructorsAndExceptions) {
    // Default constructor (zero state)
    const Integer zero;
    EXPECT_TRUE(zero.is_zero());
    EXPECT_EQ(zero.to_string(), "0");
    EXPECT_FALSE(zero.is_negative());
    EXPECT_EQ(zero.sign(), 0);
    EXPECT_FALSE(static_cast<bool>(zero));

    // Numeric constructors
    constexpr auto positive_value = 42LL;
    constexpr auto negative_value = -42LL;
    const Integer positive(positive_value);
    EXPECT_EQ(positive.to_string(), "42");
    EXPECT_EQ(positive.sign(), 1);

    const Integer negative(negative_value);
    EXPECT_EQ(negative.to_string(), "-42");
    EXPECT_TRUE(negative.is_negative());
    EXPECT_EQ(negative.sign(), -1);

    // String parsing & normalization
    const Integer padded("+000123");
    EXPECT_EQ(padded.to_string(), "123");

    const Integer negative_zero("-0");
    EXPECT_TRUE(negative_zero.is_zero()); // Zero should never be negative

    // Invalid string exceptions
    EXPECT_THROW((void)Integer(""), std::invalid_argument);
    EXPECT_THROW((void)Integer("123a45"), std::invalid_argument);
}

TEST(IntegerTest, UnaryAndIncrement) {
    constexpr auto start = 10;
    Integer number(start);

    // Unary plus and minus[cite: 1]
    EXPECT_EQ((+number).to_string(), "10");
    EXPECT_EQ((-number).to_string(), "-10");

    // Increment operators[cite: 1]
    const Integer pre_inc = ++number;
    EXPECT_EQ(pre_inc.to_string(), "11");
    EXPECT_EQ(number.to_string(), "11");

    const Integer post_inc = number++;
    EXPECT_EQ(post_inc.to_string(), "11");
    EXPECT_EQ(number.to_string(), "12");

    // Decrement operators[cite: 1]
    const Integer pre_decremented = --number;
    EXPECT_EQ(pre_decremented.to_string(), "11");
    EXPECT_EQ(number.to_string(), "11");

    const Integer post_decremented = number--;
    EXPECT_EQ(post_decremented.to_string(), "11");
    EXPECT_EQ(number.to_string(), "10");

    // Bitwise NOT (~x == -x - 1)[cite: 1]
    const Integer complement = ~Integer(0);
    EXPECT_EQ(complement.to_string(), "-1");
}

TEST(IntegerTest, ArithmeticAndDivisions) {
    // Compound assignments[cite: 1]
    constexpr auto start = 100;
    constexpr auto addend = 50;
    constexpr auto subtrahend = 30;
    constexpr auto modulus = 40;
    Integer value(start);
    value += Integer(addend);
    EXPECT_EQ(value.to_string(), "150");

    value -= Integer(subtrahend);
    EXPECT_EQ(value.to_string(), "120");

    value *= Integer(3);
    EXPECT_EQ(value.to_string(), "360");

    value /= Integer(4);
    EXPECT_EQ(value.to_string(), "90");

    value %= Integer(modulus);
    EXPECT_EQ(value.to_string(), "10");

    // Division by zero exception[cite: 1]
    constexpr auto dividend = 5;
    EXPECT_THROW((void)(Integer(dividend) / Integer(0)), std::domain_error);
    EXPECT_THROW((void)(Integer(dividend) % Integer(0)), std::domain_error);
}

TEST(IntegerTest, BitwiseAndShifts) {
    constexpr auto left_value = 12;  // 1100 in binary
    constexpr auto right_value = 10; // 1010 in binary
    const Integer left(left_value);
    const Integer right(right_value);

    EXPECT_EQ((left & right).to_string(), "8");  // 1000
    EXPECT_EQ((left | right).to_string(), "14"); // 1110
    EXPECT_EQ((left ^ right).to_string(), "6");  // 0110

    // Shifts[cite: 1]
    constexpr std::size_t shift_bits = 64; // Test cross-word shift boundary
    Integer shifted(1);
    shifted <<= shift_bits;
    EXPECT_EQ(shifted.bit_length(), 65U);

    shifted >>= shift_bits;
    EXPECT_EQ(shifted.to_string(), "1");
}

TEST(IntegerTest, BitwiseNegativeOperands) {
    // Negative operands act as infinite two's complement, as the built-in
    // integers do. Python's integers do too, and gave the expected values,
    // as "and or xor".
    struct BitwiseCase {
        const char* left;
        const char* right;
        const char* expected;
    };
    const std::vector<BitwiseCase> cases = {
        {"-12", "10", "0 -2 -2"},
        {"12", "-10", "4 -2 -6"},
        {"-12", "-10", "-12 -10 2"},
        {"-1", "0", "0 -1 -1"},
        {"-1", "-1", "-1 -1 0"},
        // Across words, and carries across words in the two's complement
        {"-18446744073709551616", "18446744073709551615", "0 -1 -1"},
        {
            "-340282366920938463463374607431768211456",
            "-18446744073709551616",
            "-340282366920938463463374607431768211456 -18446744073709551616 "
            "340282366920938463444927863358058659840",
        },
        {
            "-18446744073709551617",
            "340282366920938463481821351505477763073",
            "340282366920938463463374607431768211457 -1 "
            "-340282366920938463463374607431768211458",
        },
        {"98765432109876543210", "-1", "98765432109876543210 -1 -98765432109876543211"},
        {
            "-340282366920938463463374607431768211457",
            "12345678901234567890123",
            "12345678901234567890123 -340282366920938463463374607431768211457 "
            "-340282366920938475809053508666336101580",
        },
        {
            "-123456789012345678901234567890",
            "-98765432109876543210",
            "-123456789032558974293851995898 -78552136717259115202 "
            "123456788954006837576592880696",
        },
    };

    for (const auto& test_case : cases) {
        const Integer left(test_case.left);
        const Integer right(test_case.right);

        EXPECT_EQ(bitwise_results(left, right), test_case.expected) << test_case.left;
        EXPECT_EQ(bitwise_results(right, left), test_case.expected) << test_case.left;
        EXPECT_EQ(assigned_bitwise_results(left, right), test_case.expected) << test_case.left;
    }
}

TEST(IntegerTest, StreamsAndLiterals) {
    // Stream Output[cite: 1]
    constexpr auto output_value = 987654321987654321LL;
    const Integer test_out(output_value);
    std::ostringstream out_stream;
    out_stream << test_out;
    EXPECT_EQ(out_stream.str(), "987654321987654321");

    // Stream Input[cite: 1]
    std::istringstream in_stream("12345678901234567890");
    Integer test_in;
    in_stream >> test_in;
    EXPECT_EQ(test_in.to_string(), "12345678901234567890");

    // Failed stream input check[cite: 1]
    std::istringstream bad_stream("invalid_number");
    bad_stream >> test_in;
    EXPECT_TRUE(bad_stream.fail());

    // User-defined literal[cite: 1]
    const Integer lit = "999888777666555444"_i;
    EXPECT_EQ(lit.to_string(), "999888777666555444");
}

TEST(IntegerTest, LargeScaleStress) {
    // Compute a pseudo-factorial or large growth
    constexpr int last_factor = 30;
    Integer accumulator(1);

    for (int index = 1; index <= last_factor; ++index) {
        accumulator *= Integer(index);
    }

    // 30! = 265252859812191058636308480000000
    EXPECT_EQ(accumulator.to_string(), "265252859812191058636308480000000");
    EXPECT_GT(accumulator.bit_length(), 100U);
}
