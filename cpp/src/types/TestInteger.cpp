#include <iostream>
#include <cassert>
#include <sstream>
#include <string>
#include <stdexcept>
#include "Integer.hpp"

using namespace types;

void test_constructors_and_exceptions() {
    std::cout << "-> Testing constructors and exception handling... " << std::flush;

    // Default constructor (zero state)
    Integer a;
    assert(a.is_zero());
    assert(a.to_string() == "0");
    assert(!a.is_negative());
    assert(a.sign() == 0);
    assert(!static_cast<bool>(a));

    // Numeric constructors
    Integer b(42LL);
    assert(b.to_string() == "42");
    assert(b.sign() == 1);

    Integer c(-42LL);
    assert(c.to_string() == "-42");
    assert(c.is_negative());
    assert(c.sign() == -1);

    // String parsing & normalization
    Integer d("+000123");
    assert(d.to_string() == "123");

    Integer e("-0");
    assert(e.is_zero()); // Zero should never be negative

    // Invalid string exceptions
    bool caught_empty = false;
    try {
        Integer("");
    } catch (const std::invalid_argument&) {
        caught_empty = true;
    }
    assert(caught_empty);

    bool caught_invalid_char = false;
    try {
        Integer("123a45");
    } catch (const std::invalid_argument&) {
        caught_invalid_char = true;
    }
    assert(caught_invalid_char);

    std::cout << "PASSED\n";
}

void test_unary_and_increment() {
    std::cout << "-> Testing unary operators, increment, and decrement... " << std::flush;

    Integer x(10);

    // Unary plus and minus[cite: 1]
    assert((+x).to_string() == "10");
    assert((-x).to_string() == "-10");

    // Increment operators[cite: 1]
    Integer pre_inc = ++x;
    assert(pre_inc.to_string() == "11");
    assert(x.to_string() == "11");

    Integer post_inc = x++;
    assert(post_inc.to_string() == "11");
    assert(x.to_string() == "12");

    // Decrement operators[cite: 1]
    Integer pre_dec = --x;
    assert(pre_dec.to_string() == "11");
    assert(x.to_string() == "11");

    Integer post_dec = x--;
    assert(post_dec.to_string() == "11");
    assert(x.to_string() == "10");

    // Bitwise NOT (~x == -x - 1)[cite: 1]
    Integer not_val = ~Integer(0);
    assert(not_val.to_string() == "-1");

    std::cout << "PASSED\n";
}

void test_arithmetic_and_divisions() {
    std::cout << "-> Testing comprehensive arithmetic and division rules... " << std::flush;

    // Compound assignments[cite: 1]
    Integer val(100);
    val += Integer(50);
    assert(val.to_string() == "150");

    val -= Integer(30);
    assert(val.to_string() == "120");

    val *= Integer(3);
    assert(val.to_string() == "360");

    val /= Integer(4);
    assert(val.to_string() == "90");

    val %= Integer(40);
    assert(val.to_string() == "10");

    // Division by zero exception[cite: 1]
    bool division_by_zero_caught = false;
    try {
        Integer dummy = Integer(5) / Integer(0);
        (void)dummy;
    } catch (const std::domain_error&) {
        division_by_zero_caught = true;
    }
    assert(division_by_zero_caught);

    bool modulo_by_zero_caught = false;
    try {
        Integer dummy = Integer(5) % Integer(0);
        (void)dummy;
    } catch (const std::domain_error&) {
        modulo_by_zero_caught = true;
    }
    assert(modulo_by_zero_caught);

    std::cout << "PASSED\n";
}

void test_bitwise_and_shifts() {
    std::cout << "-> Testing bitwise and shift operators... " << std::flush;

    Integer a(12); // 1100 in binary
    Integer b(10); // 1010 in binary

    assert((a & b).to_string() == "8");   // 1000
    assert((a | b).to_string() == "14");  // 1110
    assert((a ^ b).to_string() == "6");   // 0110

    // Shifts[cite: 1]
    Integer shift_val(1);
    shift_val <<= 64; // Test cross-word shift boundary
    assert(shift_val.bit_length() == 65);

    shift_val >>= 64;
    assert(shift_val.to_string() == "1");

    std::cout << "PASSED\n";
}

void test_streams_and_literals() {
    std::cout << "-> Testing stream I/O and user-defined literals... " << std::flush;

    // Stream Output[cite: 1]
    Integer test_out(987654321987654321LL);
    std::ostringstream oss;
    oss << test_out;
    assert(oss.str() == "987654321987654321");

    // Stream Input[cite: 1]
    std::istringstream iss("12345678901234567890");
    Integer test_in;
    iss >> test_in;
    assert(test_in.to_string() == "12345678901234567890");

    // Failed stream input check[cite: 1]
    std::istringstream bad_iss("invalid_number");
    bad_iss >> test_in;
    assert(bad_iss.fail());

    // User-defined literal[cite: 1]
    Integer lit = "999888777666555444"_i;
    assert(lit.to_string() == "999888777666555444");

    std::cout << "PASSED\n";
}

void test_large_scale_stress() {
    std::cout << "-> Running large-scale multiplication and bit-length stress test... " << std::flush;

    // Compute a pseudo-factorial or large growth
    Integer accumulator(1);
    for (int i = 1; i <= 30; ++i) {
        accumulator *= Integer(i);
    }
    // 30! = 265252859812191058636308480000000
    assert(accumulator.to_string() == "265252859812191058636308480000000");
    assert(accumulator.bit_length() > 100);

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "    Running Integer Test Suite      \n";

    test_constructors_and_exceptions();
    test_unary_and_increment();
    test_arithmetic_and_divisions();
    test_bitwise_and_shifts();
    test_streams_and_literals();
    test_large_scale_stress();

    std::cout << "   All Tests Passed Successfully!   \n";

    return 0;
}
