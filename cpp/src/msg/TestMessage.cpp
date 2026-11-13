#include <iostream>
#include <cassert>
#include <string>
#include <memory>
#include <utility>

#include "Message.hpp"

using namespace msg;

void test_construction_and_value() {
    std::cout << "-> Testing construction and value()... " << std::flush;

    // Arithmetic types
    Message<int> mi(42);
    assert(mi.value() == 42);
    assert(mi.to_string() == "42");

    Message<long long> mll(-9876543210LL);
    assert(mll.value() == -9876543210LL);
    assert(mll.to_string() == "-9876543210");

    Message<double> md(3.14159);
    assert(md.value() == 3.14159);
    // std::to_string precision can vary slightly; just check it starts correctly
    assert(md.to_string().find("3.14") == 0);

    // String-like types
    Message<std::string> ms(std::string("hello"));
    assert(ms.value() == "hello");
    assert(ms.to_string() == "hello");

    Message<const char*> mcp("world");
    assert(std::string(mcp.value()) == "world");
    assert(mcp.to_string() == "world");

    // Move construction
    std::string original = "move-me";
    Message<std::string> moved(std::move(original));
    assert(moved.value() == "move-me");
    assert(moved.to_string() == "move-me");

    std::cout << "PASSED\n";
}

void test_to_string_dispatch() {
    std::cout << "-> Testing to_string() type dispatch... " << std::flush;

    // is_constructible_v path
    Message<std::string> ms("constructible");
    assert(ms.to_string() == "constructible");

    // is_arithmetic_v path
    Message<int> mi(12345);
    assert(mi.to_string() == "12345");

    Message<unsigned long> mul(999UL);
    assert(mul.to_string() == "999");

    Message<bool> mb(true);
    // std::to_string(true) == "1"
    assert(mb.to_string() == "1");

    std::cout << "PASSED\n";
}

void test_polymorphism() {
    std::cout << "-> Testing polymorphism through IMessage... " << std::flush;

    std::unique_ptr<IMessage> msg1 = std::make_unique<Message<int>>(42);
    assert(msg1->to_string() == "42");

    std::unique_ptr<IMessage> msg2 = std::make_unique<Message<std::string>>("polymorphic");
    assert(msg2->to_string() == "polymorphic");

    std::unique_ptr<IMessage> msg3 = std::make_unique<Message<long>>(-100L);
    assert(msg3->to_string() == "-100");

    // Value access still requires the concrete type
    auto* concrete = dynamic_cast<Message<int>*>(msg1.get());
    assert(concrete != nullptr);
    assert(concrete->value() == 42);

    std::cout << "PASSED\n";
}

void test_edge_cases() {
    std::cout << "-> Testing edge cases... " << std::flush;

    // Zero
    Message<int> zero(0);
    assert(zero.to_string() == "0");
    assert(zero.value() == 0);

    // Empty string
    Message<std::string> empty("");
    assert(empty.to_string().empty());
    assert(empty.value().empty());

    // Large number
    Message<long long> big(9223372036854775807LL); // LLONG_MAX
    assert(big.to_string() == "9223372036854775807");

    // Negative zero-ish (for floating point)
    Message<double> neg_zero(-0.0);
    // std::to_string(-0.0) is typically "-0.000000"
    assert(neg_zero.to_string().find("0") != std::string::npos);

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "         Running Message Test Suite               \n";

    test_construction_and_value();
    test_to_string_dispatch();
    test_polymorphism();
    test_edge_cases();

    std::cout << "      All Message Tests Passed Successfully!      \n";

    return 0;
}
