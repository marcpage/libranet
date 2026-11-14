#include <gtest/gtest.h>
#include <memory>
#include <string>
#include <utility>

#include "msg/IMessage.hpp"
#include "msg/Message.hpp"

using msg::IMessage;
using msg::Message;

TEST(MessageTest, ConstructionAndValue) {
    // Arithmetic types
    constexpr auto int_value = 42;
    const Message<int> int_message(int_value);
    EXPECT_EQ(int_message.value(), int_value);
    EXPECT_EQ(int_message.to_string(), "42");

    constexpr auto long_long_value = -9876543210LL;
    const Message<long long> long_long_message(long_long_value);
    EXPECT_EQ(long_long_message.value(), long_long_value);
    EXPECT_EQ(long_long_message.to_string(), "-9876543210");

    constexpr auto double_value = 3.14159;
    const Message<double> double_message(double_value);
    EXPECT_DOUBLE_EQ(double_message.value(), double_value);
    // std::to_string precision can vary slightly; just check it starts correctly
    EXPECT_EQ(double_message.to_string().find("3.14"), 0U);

    // String-like types
    const Message<std::string> string_message(std::string("hello"));
    EXPECT_EQ(string_message.value(), "hello");
    EXPECT_EQ(string_message.to_string(), "hello");

    const Message<const char*> char_pointer_message("world");
    EXPECT_STREQ(char_pointer_message.value(), "world");
    EXPECT_EQ(char_pointer_message.to_string(), "world");

    // Move construction
    std::string original = "move-me";
    const Message<std::string> moved(std::move(original));
    EXPECT_EQ(moved.value(), "move-me");
    EXPECT_EQ(moved.to_string(), "move-me");
}

TEST(MessageTest, ToStringDispatch) {
    // is_constructible_v path
    const Message<std::string> string_message("constructible");
    EXPECT_EQ(string_message.to_string(), "constructible");

    // is_arithmetic_v path
    constexpr auto int_value = 12345;
    const Message<int> int_message(int_value);
    EXPECT_EQ(int_message.to_string(), "12345");

    constexpr auto unsigned_long_value = 999UL;
    const Message<unsigned long> unsigned_long_message(unsigned_long_value);
    EXPECT_EQ(unsigned_long_message.to_string(), "999");

    const Message<bool> bool_message(true);
    // std::to_string(true) == "1"
    EXPECT_EQ(bool_message.to_string(), "1");
}

TEST(MessageTest, Polymorphism) {
    constexpr auto int_value = 42;
    std::unique_ptr<IMessage> msg1 = std::make_unique<Message<int>>(int_value);
    EXPECT_EQ(msg1->to_string(), "42");

    std::unique_ptr<IMessage> msg2 = std::make_unique<Message<std::string>>("polymorphic");
    EXPECT_EQ(msg2->to_string(), "polymorphic");

    constexpr auto long_value = -100L;
    std::unique_ptr<IMessage> msg3 = std::make_unique<Message<long>>(long_value);
    EXPECT_EQ(msg3->to_string(), "-100");

    // Value access still requires the concrete type
    auto* concrete = dynamic_cast<Message<int>*>(msg1.get());
    ASSERT_NE(concrete, nullptr);
    EXPECT_EQ(concrete->value(), int_value);
}

TEST(MessageTest, EdgeCases) {
    // Zero
    const Message<int> zero(0);
    EXPECT_EQ(zero.to_string(), "0");
    EXPECT_EQ(zero.value(), 0);

    // Empty string
    const Message<std::string> empty("");
    EXPECT_TRUE(empty.to_string().empty());
    EXPECT_TRUE(empty.value().empty());

    // Large number
    constexpr auto largest = 9223372036854775807LL; // LLONG_MAX
    const Message<long long> big(largest);
    EXPECT_EQ(big.to_string(), "9223372036854775807");

    // Negative zero-ish (for floating point)
    const Message<double> neg_zero(-0.0);
    // std::to_string(-0.0) is typically "-0.000000"
    EXPECT_NE(neg_zero.to_string().find('0'), std::string::npos);
}
