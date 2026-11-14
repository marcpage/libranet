#include <gtest/gtest.h>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <string>
#include <thread>
#include <vector>

#include "types/BlockingQueue.hpp"

using namespace types;
using namespace std::chrono_literals;

TEST(BlockingQueueTest, BasicOperations) {
    BlockingQueue<int> q; // unbounded by default
    EXPECT_TRUE(q.empty());
    EXPECT_EQ(q.size(), 0U);

    constexpr auto first_value = 10;
    constexpr auto second_value = 20;
    constexpr auto third_value = 30;
    q.push(first_value);
    q.push(second_value);
    q.push(third_value);

    EXPECT_FALSE(q.empty());
    EXPECT_EQ(q.size(), 3U);

    EXPECT_EQ(q.pop(), first_value);
    EXPECT_EQ(q.pop(), second_value);
    EXPECT_EQ(q.size(), 1U);
    EXPECT_EQ(q.pop(), third_value);

    EXPECT_TRUE(q.empty());
    EXPECT_EQ(q.size(), 0U);
}

TEST(BlockingQueueTest, MoveSemantics) {
    constexpr auto timeout = 10ms;
    BlockingQueue<std::string> q;

    std::string s1 = "hello";
    q.push(std::move(s1));
    EXPECT_TRUE(s1.empty()); // moved-from

    std::string s2 = "world";
    EXPECT_TRUE(q.push_ms_timeout(std::move(s2), timeout));
    EXPECT_TRUE(s2.empty());

    auto opt = q.pop_ms_timeout(timeout);
    ASSERT_TRUE(opt.has_value());
    EXPECT_EQ(*opt, "hello");

    const std::string result = q.pop(); // move out
    EXPECT_EQ(result, "world");
}

TEST(BlockingQueueTest, CapacityAndTimeouts) {
    constexpr auto timeout = 5ms;
    constexpr auto full_push_timeout = 20ms;
    constexpr auto empty_pop_timeout = 25ms;
    BlockingQueue<int> q(2); // capacity 2

    EXPECT_TRUE(q.push_ms_timeout(1, timeout));
    EXPECT_TRUE(q.push_ms_timeout(2, timeout));

    // Queue is full → timed push must fail
    EXPECT_FALSE(q.push_ms_timeout(3, full_push_timeout));
    EXPECT_EQ(q.size(), 2U);

    // Timed pop succeeds
    auto v = q.pop_ms_timeout(timeout);
    ASSERT_TRUE(v.has_value());
    EXPECT_EQ(*v, 1);
    EXPECT_EQ(q.size(), 1U);

    // Now there is room again
    EXPECT_TRUE(q.push_ms_timeout(3, timeout));
    EXPECT_EQ(q.size(), 2U);

    // Empty the queue
    EXPECT_EQ(q.pop(), 2);
    EXPECT_EQ(q.pop(), 3);
    EXPECT_TRUE(q.empty());

    // Timed pop on empty queue must time out
    const auto empty = q.pop_ms_timeout(empty_pop_timeout);
    EXPECT_FALSE(empty.has_value());
}

TEST(BlockingQueueTest, UnboundedConstant) {
    BlockingQueue<int> q1(BlockingQueue<int>::Unbounded);
    BlockingQueue<int> q2; // same as Unbounded
    constexpr int item_count = 1000;

    for (int i = 0; i < item_count; ++i) {
        q1.push(i);
        q2.push(i);
    }
    EXPECT_EQ(q1.size(), static_cast<std::size_t>(item_count));
    EXPECT_EQ(q2.size(), static_cast<std::size_t>(item_count));
}

TEST(BlockingQueueTest, BlockingBehavior) {
    BlockingQueue<int> q(1);
    constexpr auto first_value = 42;
    constexpr auto blocked_value = 99;
    constexpr auto block_wait = 30ms;

    q.push(first_value);

    std::atomic<bool> pushed{false};
    std::thread blocker([&] {
        q.push(blocked_value); // must block until space available
        pushed = true;
    });

    // Give the thread a moment to block
    std::this_thread::sleep_for(block_wait);
    EXPECT_FALSE(pushed.load());

    EXPECT_EQ(q.pop(), first_value); // frees the slot
    blocker.join();
    EXPECT_TRUE(pushed.load());
    EXPECT_EQ(q.pop(), blocked_value);
}

TEST(BlockingQueueTest, ProducerConsumer) {
    constexpr int num_items = 10'000;
    constexpr std::size_t capacity = 100; // modest
    constexpr auto pop_timeout = 50ms;
    BlockingQueue<int> q(capacity);

    std::atomic<int> produced{0};
    std::atomic<int> consumed{0};
    std::atomic<long long> sum{0};

    // Producer
    std::thread producer([&] {
        for (int i = 1; i <= num_items; ++i) {
            q.push(i);
            produced.fetch_add(1, std::memory_order_relaxed);
        }
    });

    // Two consumers
    const auto consumer_fn = [&] {
        while (consumed.load(std::memory_order_relaxed) < num_items) {
            auto item = q.pop_ms_timeout(pop_timeout);
            if (item) {
                sum.fetch_add(*item, std::memory_order_relaxed);
                consumed.fetch_add(1, std::memory_order_relaxed);
            }
        }
    };

    std::thread c1(consumer_fn);
    std::thread c2(consumer_fn);

    producer.join();
    c1.join();
    c2.join();

    EXPECT_EQ(produced.load(), num_items);
    EXPECT_EQ(consumed.load(), num_items);
    // Sum of 1..N = N*(N+1)/2
    EXPECT_EQ(sum.load(), static_cast<long long>(num_items) * (num_items + 1) / 2);
    EXPECT_TRUE(q.empty());
}

TEST(BlockingQueueTest, MultipleProducersConsumers) {
    constexpr int items_per_producer = 2000;
    constexpr int num_producers = 4;
    constexpr int num_consumers = 4;
    constexpr int total = items_per_producer * num_producers;
    constexpr std::size_t capacity = 50;
    constexpr auto pop_timeout = 100ms;

    BlockingQueue<int> q(capacity);

    std::atomic<int> consumed{0};
    std::atomic<long long> checksum{0};

    std::vector<std::thread> producers;
    producers.reserve(num_producers);
    for (int p = 0; p < num_producers; ++p) {
        producers.emplace_back([&, p] {
            for (int i = 0; i < items_per_producer; ++i) {
                q.push((p * items_per_producer) + i + 1);
            }
        });
    }

    std::vector<std::thread> consumers;
    consumers.reserve(num_consumers);
    for (int c = 0; c < num_consumers; ++c) {
        consumers.emplace_back([&] {
            while (consumed.load(std::memory_order_relaxed) < total) {
                if (auto v = q.pop_ms_timeout(pop_timeout)) {
                    checksum.fetch_add(*v, std::memory_order_relaxed);
                    consumed.fetch_add(1, std::memory_order_relaxed);
                }
            }
        });
    }

    for (auto& t : producers) {
        t.join();
    }
    for (auto& t : consumers) {
        t.join();
    }

    EXPECT_EQ(consumed.load(), total);
    // Expected sum = total*(total+1)/2
    EXPECT_EQ(checksum.load(), static_cast<long long>(total) * (total + 1) / 2);
    EXPECT_TRUE(q.empty());
}
