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

// The answers in the tests below are Python's, whose integers have no limit,
// but for quotients and remainders of negative numbers: those round toward
// zero, as C++'s own integers do.

TEST(IntegerTest, SignsOnlyAreRefused) {
    EXPECT_THROW((void)Integer("-"), std::invalid_argument);
    EXPECT_THROW((void)Integer("+"), std::invalid_argument);
}

TEST(IntegerTest, AddingAcrossAWord) {
    const Integer word_max("18446744073709551615");

    EXPECT_EQ((word_max + Integer(1)).to_string(), "18446744073709551616");
    EXPECT_EQ((word_max + Integer(1) - Integer(1)), word_max);
}

TEST(IntegerTest, AddingOppositeSigns) {
    constexpr auto small = 3;
    constexpr auto large = 10;

    EXPECT_EQ((Integer(small) + Integer(-large)).to_string(), "-7");
    EXPECT_EQ((Integer(-large) + Integer(small)).to_string(), "-7");
    EXPECT_EQ((Integer(-large) + Integer(large)).to_string(), "0");
    EXPECT_FALSE((Integer(-large) + Integer(large)).is_negative());
}

TEST(IntegerTest, ArithmeticWithZero) {
    constexpr auto number = 5;
    const Integer zero;

    EXPECT_EQ(Integer(number) + zero, Integer(number));
    EXPECT_EQ(zero + Integer(number), Integer(number));
    EXPECT_EQ(Integer(number) * zero, zero);
    EXPECT_EQ(zero * Integer(number), zero);
    EXPECT_EQ(zero / Integer(number), zero);
    EXPECT_EQ(zero % Integer(number), zero);
    EXPECT_EQ(zero.bit_length(), 0U);
}

TEST(IntegerTest, NegativeProductsAndQuotients) {
    constexpr auto factor = 6;
    constexpr auto other_factor = 7;
    constexpr auto dividend = 42;
    constexpr auto divisor = 5;

    EXPECT_EQ((Integer(-factor) * Integer(other_factor)).to_string(), "-42");
    EXPECT_EQ((Integer(-dividend) / Integer(divisor)).to_string(), "-8");
    EXPECT_EQ((Integer(-dividend) % Integer(divisor)).to_string(), "-2");
}

TEST(IntegerTest, DividingBySomethingLarger) {
    constexpr auto dividend = 5;
    constexpr auto divisor = 7;

    EXPECT_EQ((Integer(dividend) / Integer(divisor)).to_string(), "0");
    EXPECT_EQ((Integer(dividend) % Integer(divisor)).to_string(), "5");
}

// Each pair divides across more than one word.
TEST(IntegerTest, DividingLargeNumbers) {
    const Integer two_words("55340232221128654853");
    const Integer two_words_divisor("36893488147419103239");
    const Integer all_ones("340282366920938463463374607431768211455");
    const Integer just_over_a_word("18446744073709551617");
    const Integer word_max("18446744073709551615");
    const Integer large("10000000000000000000000000000000000000000");
    const Integer large_divisor("100000000000000000003");

    EXPECT_EQ((two_words / two_words_divisor).to_string(), "1");
    EXPECT_EQ((two_words % two_words_divisor).to_string(), "18446744073709551614");
    EXPECT_EQ((all_ones / just_over_a_word).to_string(), "18446744073709551615");
    EXPECT_EQ((all_ones % just_over_a_word).to_string(), "0");
    EXPECT_EQ((all_ones / word_max).to_string(), "18446744073709551617");
    EXPECT_EQ((all_ones % word_max).to_string(), "0");
    EXPECT_EQ((large / large_divisor).to_string(), "99999999999999999997");
    EXPECT_EQ((large % large_divisor).to_string(), "9");
}

// The first guess at this quotient, from the top words alone, is too large,
// and long division takes it down.
TEST(IntegerTest, DividingCorrectsAGuessTooLarge) {
    const Integer dividend("2669368218809897665185599758185480353793330886545799213353");
    const Integer divisor("263044675155055653665554299596658041639");

    EXPECT_EQ((dividend / divisor).to_string(), "10147965235321331945");
    EXPECT_EQ((dividend % divisor).to_string(), "207887199098221082465069622361048355498");
}

TEST(IntegerTest, ComparingAcrossWords) {
    const Integer two_words("18446744073709551616");

    EXPECT_LT(Integer(1), two_words);
    EXPECT_GT(two_words, Integer(1));
}

TEST(IntegerTest, ShiftsWithinWords) {
    constexpr std::size_t across_a_word = 70;
    constexpr std::size_t few = 3;
    constexpr std::size_t word = 64;
    constexpr std::size_t past_the_end = 200;
    const auto large = Integer(1) << across_a_word;

    EXPECT_EQ(large.to_string(), "1180591620717411303424");
    EXPECT_EQ((large >> few).to_string(), "147573952589676412928");
    EXPECT_EQ((large >> word).to_string(), "64");
    EXPECT_EQ((large >> past_the_end).to_string(), "0");
    EXPECT_EQ((Integer("9223372036854775808") << 1).to_string(), "18446744073709551616");
    EXPECT_EQ(large << 0, large);
    EXPECT_EQ(large >> 0, large);
    EXPECT_EQ(Integer() << few, Integer());
    EXPECT_EQ(Integer() >> few, Integer());
}

// & and | of two negative numbers, in two's complement, as Python gives
// them.
TEST(IntegerTest, BitwiseOfNegatives) {
    constexpr auto left = -12;
    constexpr auto right = -10;

    EXPECT_EQ((Integer(left) & Integer(right)).to_string(), "-12");
    EXPECT_EQ((Integer(left) | Integer(right)).to_string(), "-10");
}

TEST(IntegerTest, AddingAShorterNumber) {
    const Integer two_words("18446744073709551616");

    EXPECT_EQ((two_words + Integer(1)).to_string(), "18446744073709551617");
    EXPECT_EQ((Integer(1) + two_words).to_string(), "18446744073709551617");
}

// &, |, and ^ of two positive numbers, one a word longer than the other.
TEST(IntegerTest, BitwiseOfDifferentLengths) {
    constexpr auto short_number = 3;
    const Integer long_number("18446744073709551617");

    EXPECT_EQ(bitwise_results(long_number, Integer(short_number)),
              "1 18446744073709551619 18446744073709551618");
    EXPECT_EQ(bitwise_results(Integer(short_number), long_number),
              "1 18446744073709551619 18446744073709551618");
}

TEST(IntegerTest, ComparingSigns) {
    constexpr auto small = 1;
    constexpr auto large = 2;

    EXPECT_NE(Integer(small), Integer(-small));
    EXPECT_NE(Integer(small), Integer(large));
    EXPECT_LT(Integer(-small), Integer(small));
    EXPECT_FALSE(Integer(small) < Integer(-small));
    EXPECT_LT(Integer(-large), Integer(-small));
    EXPECT_FALSE(Integer(-small) < Integer(-large));
}

TEST(IntegerTest, NothingToStreamIn) {
    constexpr auto number = 5;
    std::istringstream in_stream("");
    Integer value(number);

    in_stream >> value;

    EXPECT_TRUE(in_stream.fail());
    EXPECT_EQ(value, Integer(number));
}
