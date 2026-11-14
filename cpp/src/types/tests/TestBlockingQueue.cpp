#include <gtest/gtest.h>
#include <atomic>
#include <chrono> // IWYU pragma: keep (the ms literals)
#include <cstddef>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "types/BlockingQueue.hpp"

using types::BlockingQueue;
using namespace std::chrono_literals;

// The tests read optionals after ASSERT_TRUE(x.has_value()), which this
// check does not follow, or with value(), whose exception gtest reports as
// a failure.
// NOLINTBEGIN(bugprone-unchecked-optional-access)

TEST(BlockingQueueTest, BasicOperations) {
    BlockingQueue<int> queue; // unbounded by default
    EXPECT_TRUE(queue.empty());
    EXPECT_EQ(queue.size(), 0U);

    constexpr auto first_value = 10;
    constexpr auto second_value = 20;
    constexpr auto third_value = 30;
    queue.push(first_value);
    queue.push(second_value);
    queue.push(third_value);

    EXPECT_FALSE(queue.empty());
    EXPECT_EQ(queue.size(), 3U);

    EXPECT_EQ(queue.pop(), first_value);
    EXPECT_EQ(queue.pop(), second_value);
    EXPECT_EQ(queue.size(), 1U);
    EXPECT_EQ(queue.pop(), third_value);

    EXPECT_TRUE(queue.empty());
    EXPECT_EQ(queue.size(), 0U);
}

// This test reads strings after moving them, to see that push() emptied them.
// NOLINTBEGIN(bugprone-use-after-move)
TEST(BlockingQueueTest, MoveSemantics) {
    constexpr auto timeout = 10ms;
    BlockingQueue<std::string> queue;

    std::string first_text = "hello";
    queue.push(std::move(first_text));
    EXPECT_TRUE(first_text.empty()); // moved-from

    std::string second_text = "world";
    EXPECT_TRUE(queue.push_ms_timeout(std::move(second_text), timeout));
    EXPECT_TRUE(second_text.empty());

    auto opt = queue.pop_ms_timeout(timeout);
    ASSERT_TRUE(opt.has_value());
    EXPECT_EQ(*opt, "hello");

    const std::string result = queue.pop(); // move out
    EXPECT_EQ(result, "world");
}
// NOLINTEND(bugprone-use-after-move)

TEST(BlockingQueueTest, CapacityAndTimeouts) {
    constexpr auto timeout = 5ms;
    constexpr auto full_push_timeout = 20ms;
    constexpr auto empty_pop_timeout = 25ms;
    BlockingQueue<int> queue(2); // capacity 2

    EXPECT_TRUE(queue.push_ms_timeout(1, timeout));
    EXPECT_TRUE(queue.push_ms_timeout(2, timeout));

    // Queue is full → timed push must fail
    EXPECT_FALSE(queue.push_ms_timeout(3, full_push_timeout));
    EXPECT_EQ(queue.size(), 2U);

    // Timed pop succeeds
    auto item = queue.pop_ms_timeout(timeout);
    ASSERT_TRUE(item.has_value());
    EXPECT_EQ(*item, 1);
    EXPECT_EQ(queue.size(), 1U);

    // Now there is room again
    EXPECT_TRUE(queue.push_ms_timeout(3, timeout));
    EXPECT_EQ(queue.size(), 2U);

    // Empty the queue
    EXPECT_EQ(queue.pop(), 2);
    EXPECT_EQ(queue.pop(), 3);
    EXPECT_TRUE(queue.empty());

    // Timed pop on empty queue must time out
    const auto empty = queue.pop_ms_timeout(empty_pop_timeout);
    EXPECT_FALSE(empty.has_value());
}

TEST(BlockingQueueTest, UnboundedConstant) {
    BlockingQueue<int> unbounded_queue(BlockingQueue<int>::Unbounded);
    BlockingQueue<int> default_queue; // same as Unbounded
    constexpr int item_count = 1000;

    for (int i = 0; i < item_count; ++i) {
        unbounded_queue.push(i);
        default_queue.push(i);
    }
    EXPECT_EQ(unbounded_queue.size(), static_cast<std::size_t>(item_count));
    EXPECT_EQ(default_queue.size(), static_cast<std::size_t>(item_count));
}

TEST(BlockingQueueTest, BlockingBehavior) {
    BlockingQueue<int> queue(1);
    constexpr auto first_value = 42;
    constexpr auto blocked_value = 99;
    constexpr auto block_wait = 30ms;

    queue.push(first_value);

    std::atomic<bool> pushed{false};
    std::thread blocker([&] {
        queue.push(blocked_value); // must block until space available
        pushed = true;
    });

    // Give the thread a moment to block
    std::this_thread::sleep_for(block_wait);
    EXPECT_FALSE(pushed.load());

    EXPECT_EQ(queue.pop(), first_value); // frees the slot
    blocker.join();
    EXPECT_TRUE(pushed.load());
    EXPECT_EQ(queue.pop(), blocked_value);
}

TEST(BlockingQueueTest, ProducerConsumer) {
    constexpr int num_items = 10'000;
    constexpr std::size_t capacity = 100; // modest
    constexpr auto pop_timeout = 50ms;
    BlockingQueue<int> queue(capacity);

    std::atomic<int> produced{0};
    std::atomic<int> consumed{0};
    std::atomic<long long> sum{0};

    // Producer
    std::thread producer([&] {
        for (int i = 1; i <= num_items; ++i) {
            queue.push(i);
            produced.fetch_add(1, std::memory_order_relaxed);
        }
    });

    // Two consumers
    const auto consumer_fn = [&] {
        while (consumed.load(std::memory_order_relaxed) < num_items) {
            auto item = queue.pop_ms_timeout(pop_timeout);
            if (item) {
                sum.fetch_add(*item, std::memory_order_relaxed);
                consumed.fetch_add(1, std::memory_order_relaxed);
            }
        }
    };

    std::thread first_consumer(consumer_fn);
    std::thread second_consumer(consumer_fn);

    producer.join();
    first_consumer.join();
    second_consumer.join();

    EXPECT_EQ(produced.load(), num_items);
    EXPECT_EQ(consumed.load(), num_items);
    // Sum of 1..N = N*(N+1)/2
    EXPECT_EQ(sum.load(), static_cast<long long>(num_items) * (num_items + 1) / 2);
    EXPECT_TRUE(queue.empty());
}

// This test's threads and nested loops, and gtest's assertion macros, which
// count as branches, put it over this check's limit.
// NOLINTBEGIN(readability-function-cognitive-complexity)
TEST(BlockingQueueTest, MultipleProducersConsumers) {
    constexpr int items_per_producer = 2000;
    constexpr int num_producers = 4;
    constexpr int num_consumers = 4;
    constexpr int total = items_per_producer * num_producers;
    constexpr std::size_t capacity = 50;
    constexpr auto pop_timeout = 100ms;

    BlockingQueue<int> queue(capacity);

    std::atomic<int> consumed{0};
    std::atomic<long long> checksum{0};

    std::vector<std::thread> producers;
    producers.reserve(num_producers);
    for (int producer_index = 0; producer_index < num_producers; ++producer_index) {
        producers.emplace_back([&, producer_index] {
            for (int i = 0; i < items_per_producer; ++i) {
                queue.push((producer_index * items_per_producer) + i + 1);
            }
        });
    }

    std::vector<std::thread> consumers;
    consumers.reserve(num_consumers);
    for (int consumer_index = 0; consumer_index < num_consumers; ++consumer_index) {
        consumers.emplace_back([&] {
            while (consumed.load(std::memory_order_relaxed) < total) {
                if (auto item = queue.pop_ms_timeout(pop_timeout)) {
                    checksum.fetch_add(*item, std::memory_order_relaxed);
                    consumed.fetch_add(1, std::memory_order_relaxed);
                }
            }
        });
    }

    for (auto& producer : producers) {
        producer.join();
    }
    for (auto& consumer : consumers) {
        consumer.join();
    }

    EXPECT_EQ(consumed.load(), total);
    // Expected sum = total*(total+1)/2
    EXPECT_EQ(checksum.load(), static_cast<long long>(total) * (total + 1) / 2);
    EXPECT_TRUE(queue.empty());
}
// NOLINTEND(readability-function-cognitive-complexity)

// NOLINTEND(bugprone-unchecked-optional-access)
