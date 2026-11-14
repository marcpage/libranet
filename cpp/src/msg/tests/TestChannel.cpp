#include <gtest/gtest.h>
#include <atomic>
#include <chrono>
#include <string>
#include <thread>

#include "msg/Channel.hpp"

using msg::Channel;
using namespace std::chrono_literals;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

namespace {

void sleep_ms(int duration_ms) {
    std::this_thread::sleep_for(std::chrono::milliseconds(duration_ms));
}

} // namespace

// ---------------------------------------------------------------------------
// Test cases
// ---------------------------------------------------------------------------

// The tests read optionals after ASSERT_TRUE(x.has_value()), which this
// check does not follow, or with value(), whose exception gtest reports as
// a failure.
// NOLINTBEGIN(bugprone-unchecked-optional-access)

TEST(ChannelTest, ConstructionAndBasicOperations) {
    Channel channel("test.basic");

    EXPECT_TRUE(channel.empty());
    EXPECT_EQ(channel.size(), 0U);

    constexpr auto posted_value = 42;
    Channel::post("test.basic", posted_value);
    EXPECT_FALSE(channel.empty());
    EXPECT_EQ(channel.size(), 1U);

    const auto msg = channel.next_message();
    EXPECT_TRUE(msg.has_value());
    EXPECT_TRUE(channel.empty());
    EXPECT_EQ(channel.size(), 0U);

    // Typed path
    Channel::post("test.basic", std::string("hello"));
    auto value = channel.next<std::string>();
    ASSERT_TRUE(value.has_value());
    EXPECT_EQ(*value, "hello");
    EXPECT_TRUE(channel.empty());
}

TEST(ChannelTest, TimeoutBehaviour) {
    Channel channel("test.timeout");

    // Immediate timeout
    const auto timed_out = channel.next_message(0ms);
    EXPECT_FALSE(timed_out.has_value());

    const auto timed_out_typed = channel.next<int>(0ms);
    EXPECT_FALSE(timed_out_typed.has_value());

    // Short timeout with no message
    constexpr auto short_timeout = 20ms;
    const auto still_timeout = channel.next_message(short_timeout);
    EXPECT_FALSE(still_timeout.has_value());

    // Post then consume with timeout
    constexpr auto posted_value = 99;
    constexpr auto timeout = 50ms;
    Channel::post("test.timeout", posted_value);
    const auto got = channel.next_message(timeout);
    EXPECT_TRUE(got.has_value());

    const auto typed = channel.next<int>(timeout); // queue is empty again
    EXPECT_FALSE(typed.has_value());
}

TEST(ChannelTest, TypeMismatch) {
    Channel channel("test.types");

    constexpr auto posted_value = 12345;
    constexpr auto timeout = 10ms;
    Channel::post("test.types", posted_value);

    // Correct type
    auto match = channel.next<int>(timeout);
    ASSERT_TRUE(match.has_value());
    EXPECT_EQ(*match, posted_value);

    // Wrong type after another post
    Channel::post("test.types", std::string("not an int"));
    const auto mismatch = channel.next<int>(timeout);
    EXPECT_FALSE(mismatch.has_value());

    // The message is still consumed by next_message / next
    // (we chose to treat type mismatch as "no usable value")
    EXPECT_TRUE(channel.empty());
}

TEST(ChannelTest, BroadcastToMultipleSubscribers) {
    Channel channel_a("test.broadcast");
    Channel channel_b("test.broadcast");
    const Channel other_channel("test.other"); // different name – should not receive

    constexpr auto posted_value = 7;
    Channel::post("test.broadcast", posted_value);

    EXPECT_EQ(channel_a.size(), 1U);
    EXPECT_EQ(channel_b.size(), 1U);
    EXPECT_TRUE(other_channel.empty());

    auto value_a = channel_a.next<int>();
    auto value_b = channel_b.next<int>();
    ASSERT_TRUE(value_a.has_value());
    EXPECT_EQ(*value_a, posted_value);
    ASSERT_TRUE(value_b.has_value());
    EXPECT_EQ(*value_b, posted_value);
    EXPECT_TRUE(other_channel.empty());
}

TEST(ChannelTest, DestructorRemovesSubscriber) {
    {
        const Channel temporary("test.lifetime");
        Channel::post("test.lifetime", 1);
        EXPECT_EQ(temporary.size(), 1U);
    } // temporary destroyed here

    // Posting after destruction must not crash and must not deliver anywhere
    Channel::post("test.lifetime", 2);

    // A new channel with the same name starts empty
    const Channel fresh("test.lifetime");
    EXPECT_TRUE(fresh.empty());
}

TEST(ChannelTest, WaitForeverAndConcurrentProducer) {
    Channel channel("test.concurrent");
    std::atomic<bool> producer_done{false};
    constexpr auto block_wait_ms = 30;
    constexpr auto posted_value = 55;

    std::thread producer([&] {
        sleep_ms(block_wait_ms); // give the consumer a moment to block
        Channel::post("test.concurrent", posted_value);
        producer_done = true;
    });

    // This blocks until the producer posts
    const auto value = channel.next<int>(Channel::WaitForever);
    EXPECT_TRUE(value.has_value());
    EXPECT_EQ(value, posted_value);

    // The producer sets producer_done after posting, so it is only certain to
    // be set once the producer has finished.
    producer.join();
    EXPECT_TRUE(producer_done.load());
}

TEST(ChannelTest, MultipleMessagesFifoOrder) {
    Channel channel("test.fifo");

    constexpr auto first_value = 10;
    constexpr auto second_value = 20;
    constexpr auto third_value = 30;
    Channel::post("test.fifo", first_value);
    Channel::post("test.fifo", second_value);
    Channel::post("test.fifo", third_value);

    EXPECT_EQ(channel.size(), 3U);

    EXPECT_EQ(channel.next<int>(), first_value);
    EXPECT_EQ(channel.next<int>(), second_value);
    EXPECT_EQ(channel.next<int>(), third_value);
    EXPECT_TRUE(channel.empty());
}

TEST(ChannelTest, MixedTypesAndToString) {
    Channel channel("test.mixed");

    constexpr auto posted_value = 42;
    Channel::post("test.mixed", posted_value);
    Channel::post("test.mixed", std::string("forty-two"));

    auto raw1 = channel.next_message();
    ASSERT_TRUE(raw1.has_value());
    EXPECT_EQ(raw1.value()->to_string(), "42");

    auto raw2 = channel.next_message();
    ASSERT_TRUE(raw2.has_value());
    EXPECT_EQ(raw2.value()->to_string(), "forty-two");
}

// NOLINTEND(bugprone-unchecked-optional-access)
