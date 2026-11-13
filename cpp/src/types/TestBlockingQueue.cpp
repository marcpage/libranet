#include <gtest/gtest.h>
#include <thread>
#include <vector>
#include <atomic>
#include <chrono>
#include <string>
#include "BlockingQueue.hpp"

using namespace types;
using namespace std::chrono_literals;

TEST(BlockingQueueTest, BasicOperations) {
    BlockingQueue<int> q; // unbounded by default
    EXPECT_TRUE(q.empty());
    EXPECT_EQ(q.size(), 0U);

    q.push(10);
    q.push(20);
    q.push(30);

    EXPECT_FALSE(q.empty());
    EXPECT_EQ(q.size(), 3U);

    EXPECT_EQ(q.pop(), 10);
    EXPECT_EQ(q.pop(), 20);
    EXPECT_EQ(q.size(), 1U);
    EXPECT_EQ(q.pop(), 30);

    EXPECT_TRUE(q.empty());
    EXPECT_EQ(q.size(), 0U);
}

TEST(BlockingQueueTest, MoveSemantics) {
    BlockingQueue<std::string> q;

    std::string s1 = "hello";
    q.push(std::move(s1));
    EXPECT_TRUE(s1.empty()); // moved-from

    std::string s2 = "world";
    EXPECT_TRUE(q.push_ms_timeout(std::move(s2), 10ms));
    EXPECT_TRUE(s2.empty());

    auto opt = q.pop_ms_timeout(10ms);
    ASSERT_TRUE(opt.has_value());
    EXPECT_EQ(*opt, "hello");

    std::string result = q.pop(); // move out
    EXPECT_EQ(result, "world");
}

TEST(BlockingQueueTest, CapacityAndTimeouts) {
    BlockingQueue<int> q(2); // capacity 2

    EXPECT_TRUE(q.push_ms_timeout(1, 5ms));
    EXPECT_TRUE(q.push_ms_timeout(2, 5ms));

    // Queue is full → timed push must fail
    EXPECT_FALSE(q.push_ms_timeout(3, 20ms));
    EXPECT_EQ(q.size(), 2U);

    // Timed pop succeeds
    auto v = q.pop_ms_timeout(5ms);
    ASSERT_TRUE(v.has_value());
    EXPECT_EQ(*v, 1);
    EXPECT_EQ(q.size(), 1U);

    // Now there is room again
    EXPECT_TRUE(q.push_ms_timeout(3, 5ms));
    EXPECT_EQ(q.size(), 2U);

    // Empty the queue
    EXPECT_EQ(q.pop(), 2);
    EXPECT_EQ(q.pop(), 3);
    EXPECT_TRUE(q.empty());

    // Timed pop on empty queue must time out
    auto empty = q.pop_ms_timeout(25ms);
    EXPECT_FALSE(empty.has_value());
}

TEST(BlockingQueueTest, UnboundedConstant) {
    BlockingQueue<int> q1(BlockingQueue<int>::Unbounded);
    BlockingQueue<int> q2; // same as Unbounded

    for (int i = 0; i < 1000; ++i) {
        q1.push(i);
        q2.push(i);
    }
    EXPECT_EQ(q1.size(), 1000U);
    EXPECT_EQ(q2.size(), 1000U);
}

TEST(BlockingQueueTest, BlockingBehavior) {
    BlockingQueue<int> q(1);

    q.push(42);

    std::atomic<bool> pushed{false};
    std::thread blocker([&] {
        q.push(99); // must block until space available
        pushed = true;
    });

    // Give the thread a moment to block
    std::this_thread::sleep_for(30ms);
    EXPECT_FALSE(pushed.load());

    EXPECT_EQ(q.pop(), 42); // frees the slot
    blocker.join();
    EXPECT_TRUE(pushed.load());
    EXPECT_EQ(q.pop(), 99);
}

TEST(BlockingQueueTest, ProducerConsumer) {
    constexpr int num_items = 10'000;
    BlockingQueue<int> q(100); // modest capacity

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
    auto consumer_fn = [&] {
        while (consumed.load(std::memory_order_relaxed) < num_items) {
            auto item = q.pop_ms_timeout(50ms);
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

    BlockingQueue<int> q(50);

    std::atomic<int> consumed{0};
    std::atomic<long long> checksum{0};

    std::vector<std::thread> producers;
    for (int p = 0; p < num_producers; ++p) {
        producers.emplace_back([&, p] {
            for (int i = 0; i < items_per_producer; ++i) {
                q.push(p * items_per_producer + i + 1);
            }
        });
    }

    std::vector<std::thread> consumers;
    for (int c = 0; c < num_consumers; ++c) {
        consumers.emplace_back([&] {
            while (consumed.load(std::memory_order_relaxed) < total) {
                if (auto v = q.pop_ms_timeout(100ms)) {
                    checksum.fetch_add(*v, std::memory_order_relaxed);
                    consumed.fetch_add(1, std::memory_order_relaxed);
                }
            }
        });
    }

    for (auto& t : producers) t.join();
    for (auto& t : consumers) t.join();

    EXPECT_EQ(consumed.load(), total);
    // Expected sum = total*(total+1)/2
    EXPECT_EQ(checksum.load(), static_cast<long long>(total) * (total + 1) / 2);
    EXPECT_TRUE(q.empty());
}
