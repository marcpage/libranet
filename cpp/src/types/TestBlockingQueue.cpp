#include <iostream>
#include <cassert>
#include <thread>
#include <vector>
#include <atomic>
#include <chrono>
#include <string>
#include "BlockingQueue.hpp"

using namespace types;
using namespace std::chrono_literals;

void test_basic_operations() {
    std::cout << "-> Testing basic push/pop, size, empty... " << std::flush;

    BlockingQueue<int> q;                       // unbounded by default
    assert(q.empty());
    assert(q.size() == 0);

    q.push(10);
    q.push(20);
    q.push(30);

    assert(!q.empty());
    assert(q.size() == 3);

    assert(q.pop() == 10);
    assert(q.pop() == 20);
    assert(q.size() == 1);
    assert(q.pop() == 30);

    assert(q.empty());
    assert(q.size() == 0);

    std::cout << "PASSED\n";
}

void test_move_semantics() {
    std::cout << "-> Testing move push and move pop... " << std::flush;

    BlockingQueue<std::string> q;

    std::string s1 = "hello";
    q.push(std::move(s1));
    assert(s1.empty());                         // moved-from

    std::string s2 = "world";
    assert(q.push_ms_timeout(std::move(s2), 10ms));
    assert(s2.empty());

    auto opt = q.pop_ms_timeout(10ms);
    assert(opt.has_value());
    assert(*opt == "hello");

    std::string result = q.pop();               // move out
    assert(result == "world");

    std::cout << "PASSED\n";
}

void test_capacity_and_timeouts() {
    std::cout << "-> Testing capacity limit and timeouts... " << std::flush;

    BlockingQueue<int> q(2);                    // capacity 2

    assert(q.push_ms_timeout(1, 5ms));
    assert(q.push_ms_timeout(2, 5ms));

    // Queue is full → timed push must fail
    assert(!q.push_ms_timeout(3, 20ms));
    assert(q.size() == 2);

    // Timed pop succeeds
    auto v = q.pop_ms_timeout(5ms);
    assert(v.has_value() && *v == 1);
    assert(q.size() == 1);

    // Now there is room again
    assert(q.push_ms_timeout(3, 5ms));
    assert(q.size() == 2);

    // Empty the queue
    assert(q.pop() == 2);
    assert(q.pop() == 3);
    assert(q.empty());

    // Timed pop on empty queue must time out
    auto empty = q.pop_ms_timeout(25ms);
    assert(!empty.has_value());

    std::cout << "PASSED\n";
}

void test_unbounded_constant() {
    std::cout << "-> Testing Unbounded constant... " << std::flush;

    BlockingQueue<int> q1(BlockingQueue<int>::Unbounded);
    BlockingQueue<int> q2;                      // same as Unbounded

    for (int i = 0; i < 1000; ++i) {
        q1.push(i);
        q2.push(i);
    }
    assert(q1.size() == 1000);
    assert(q2.size() == 1000);

    std::cout << "PASSED\n";
}

void test_producer_consumer() {
    std::cout << "-> Testing multi-threaded producer/consumer... " << std::flush;

    constexpr int num_items = 10'000;
    BlockingQueue<int> q(100);                  // modest capacity

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

    assert(produced == num_items);
    assert(consumed == num_items);
    // Sum of 1..N = N*(N+1)/2
    assert(sum == static_cast<long long>(num_items) * (num_items + 1) / 2);
    assert(q.empty());

    std::cout << "PASSED\n";
}

void test_blocking_behavior() {
    std::cout << "-> Testing true blocking (no timeout)... " << std::flush;

    BlockingQueue<int> q(1);

    q.push(42);

    std::atomic<bool> pushed{false};
    std::thread blocker([&] {
        q.push(99);                             // must block until space available
        pushed = true;
    });

    // Give the thread a moment to block
    std::this_thread::sleep_for(30ms);
    assert(!pushed.load());

    assert(q.pop() == 42);                      // frees the slot
    blocker.join();
    assert(pushed.load());
    assert(q.pop() == 99);

    std::cout << "PASSED\n";
}

void test_multiple_producers_consumers() {
    std::cout << "-> Testing many producers and consumers... " << std::flush;

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

    assert(consumed == total);
    // Expected sum = total*(total+1)/2
    assert(checksum == static_cast<long long>(total) * (total + 1) / 2);
    assert(q.empty());

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "       Running BlockingQueue Test Suite           \n";

    test_basic_operations();
    test_move_semantics();
    test_capacity_and_timeouts();
    test_unbounded_constant();
    test_blocking_behavior();
    test_producer_consumer();
    test_multiple_producers_consumers();

    std::cout << "   All BlockingQueue Tests Passed Successfully!   \n";

    return 0;
}
