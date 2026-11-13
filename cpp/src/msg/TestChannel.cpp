#include <gtest/gtest.h>
#include <string>
#include <thread>
#include <chrono>
#include <vector>
#include <atomic>

#include "Channel.hpp"
#include "Message.hpp"

using namespace msg;
using namespace std::chrono_literals;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static void sleep_ms(int ms) {
    std::this_thread::sleep_for(std::chrono::milliseconds(ms));
}

// ---------------------------------------------------------------------------
// Test cases
// ---------------------------------------------------------------------------

TEST(ChannelTest, ConstructionAndBasicOperations) {
    Channel ch("test.basic");

    EXPECT_TRUE(ch.empty());
    EXPECT_EQ(ch.size(), 0U);

    Channel::post("test.basic", 42);
    EXPECT_FALSE(ch.empty());
    EXPECT_EQ(ch.size(), 1U);

    auto msg = ch.next_message();
    EXPECT_TRUE(msg.has_value());
    EXPECT_TRUE(ch.empty());
    EXPECT_EQ(ch.size(), 0U);

    // Typed path
    Channel::post("test.basic", std::string("hello"));
    auto value = ch.next<std::string>();
    ASSERT_TRUE(value.has_value());
    EXPECT_EQ(*value, "hello");
    EXPECT_TRUE(ch.empty());
}

TEST(ChannelTest, TimeoutBehaviour) {
    Channel ch("test.timeout");

    // Immediate timeout
    auto timed_out = ch.next_message(0ms);
    EXPECT_FALSE(timed_out.has_value());

    auto timed_out_typed = ch.next<int>(0ms);
    EXPECT_FALSE(timed_out_typed.has_value());

    // Short timeout with no message
    auto still_timeout = ch.next_message(20ms);
    EXPECT_FALSE(still_timeout.has_value());

    // Post then consume with timeout
    Channel::post("test.timeout", 99);
    auto got = ch.next_message(50ms);
    EXPECT_TRUE(got.has_value());

    auto typed = ch.next<int>(50ms); // queue is empty again
    EXPECT_FALSE(typed.has_value());
}

TEST(ChannelTest, TypeMismatch) {
    Channel ch("test.types");

    Channel::post("test.types", 12345);

    // Correct type
    auto ok = ch.next<int>(10ms);
    ASSERT_TRUE(ok.has_value());
    EXPECT_EQ(*ok, 12345);

    // Wrong type after another post
    Channel::post("test.types", std::string("not an int"));
    auto mismatch = ch.next<int>(10ms);
    EXPECT_FALSE(mismatch.has_value());

    // The message is still consumed by next_message / next
    // (we chose to treat type mismatch as "no usable value")
    EXPECT_TRUE(ch.empty());
}

TEST(ChannelTest, BroadcastToMultipleSubscribers) {
    Channel a("test.broadcast");
    Channel b("test.broadcast");
    Channel c("test.other"); // different name – should not receive

    Channel::post("test.broadcast", 7);

    EXPECT_EQ(a.size(), 1U);
    EXPECT_EQ(b.size(), 1U);
    EXPECT_TRUE(c.empty());

    auto va = a.next<int>();
    auto vb = b.next<int>();
    ASSERT_TRUE(va.has_value());
    EXPECT_EQ(*va, 7);
    ASSERT_TRUE(vb.has_value());
    EXPECT_EQ(*vb, 7);
    EXPECT_TRUE(c.empty());
}

TEST(ChannelTest, DestructorRemovesSubscriber) {
    {
        Channel temporary("test.lifetime");
        Channel::post("test.lifetime", 1);
        EXPECT_EQ(temporary.size(), 1U);
    } // temporary destroyed here

    // Posting after destruction must not crash and must not deliver anywhere
    Channel::post("test.lifetime", 2);

    // A new channel with the same name starts empty
    Channel fresh("test.lifetime");
    EXPECT_TRUE(fresh.empty());
}

TEST(ChannelTest, WaitForeverAndConcurrentProducer) {
    Channel ch("test.concurrent");
    std::atomic<bool> producer_done{false};

    std::thread producer([&] {
        sleep_ms(30); // give the consumer a moment to block
        Channel::post("test.concurrent", 55);
        producer_done = true;
    });

    // This blocks until the producer posts
    auto value = ch.next<int>(Channel::WaitForever);
    EXPECT_TRUE(value.has_value());
    EXPECT_EQ(value, 55);

    // The producer sets producer_done after posting, so it is only certain to
    // be set once the producer has finished.
    producer.join();
    EXPECT_TRUE(producer_done.load());
}

TEST(ChannelTest, MultipleMessagesFifoOrder) {
    Channel ch("test.fifo");

    Channel::post("test.fifo", 10);
    Channel::post("test.fifo", 20);
    Channel::post("test.fifo", 30);

    EXPECT_EQ(ch.size(), 3U);

    EXPECT_EQ(ch.next<int>(), 10);
    EXPECT_EQ(ch.next<int>(), 20);
    EXPECT_EQ(ch.next<int>(), 30);
    EXPECT_TRUE(ch.empty());
}

TEST(ChannelTest, MixedTypesAndToString) {
    Channel ch("test.mixed");

    Channel::post("test.mixed", 42);
    Channel::post("test.mixed", std::string("forty-two"));

    auto raw1 = ch.next_message();
    ASSERT_TRUE(raw1.has_value());
    EXPECT_EQ(raw1.value()->to_string(), "42");

    auto raw2 = ch.next_message();
    ASSERT_TRUE(raw2.has_value());
    EXPECT_EQ(raw2.value()->to_string(), "forty-two");
}
