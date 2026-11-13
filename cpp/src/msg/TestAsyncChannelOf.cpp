#include <iostream>
#include <cassert>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "AsyncChannelOf.hpp"
#include "Message.hpp"

using namespace msg;
using namespace std::chrono_literals;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

template <typename Predicate>
static bool wait_until(Predicate predicate, std::chrono::milliseconds timeout = 500ms) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        if (predicate()) {
            return true;
        }
        std::this_thread::sleep_for(1ms);
    }
    return predicate();
}

// ---------------------------------------------------------------------------
// Test cases
// ---------------------------------------------------------------------------

void test_construction_and_callback() {
    std::cout << "-> Testing construction and typed asynchronous callback... " << std::flush;

    std::mutex mutex;
    std::condition_variable condition;
    std::vector<int> received;

    {
        AsyncChannelOf<int> ch("test.asyncof.basic", [&](int value) {
            {
                std::lock_guard<std::mutex> lock(mutex);
                received.push_back(value);
            }
            condition.notify_one();
        }, nullptr);

        assert(ch.empty());

        Channel::post("test.asyncof.basic", 42);

        std::unique_lock<std::mutex> lock(mutex);
        const bool delivered = condition.wait_for(lock, 500ms, [&] {
            return received.size() == 1;
        });
        assert(delivered);
        assert(received[0] == 42);
    }

    std::cout << "PASSED\n";
}

void test_multiple_messages_fifo_order() {
    std::cout << "-> Testing asynchronous FIFO delivery with typed messages... " << std::flush;

    std::mutex mutex;
    std::condition_variable condition;
    std::vector<int> received;

    {
        AsyncChannelOf<int> ch("test.asyncof.fifo", [&](int value) {
            {
                std::lock_guard<std::mutex> lock(mutex);
                received.push_back(value);
            }
            condition.notify_one();
        }, nullptr);

        Channel::post("test.asyncof.fifo", 10);
        Channel::post("test.asyncof.fifo", 20);
        Channel::post("test.asyncof.fifo", 30);

        std::unique_lock<std::mutex> lock(mutex);
        const bool delivered = condition.wait_for(lock, 500ms, [&] {
            return received.size() == 3;
        });
        assert(delivered);
        assert(received[0] == 10);
        assert(received[1] == 20);
        assert(received[2] == 30);
    }

    std::cout << "PASSED\n";
}

void test_broadcast_to_multiple_async_channels() {
    std::cout << "-> Testing broadcast to multiple typed async channels... " << std::flush;

    std::mutex mutex;
    std::condition_variable condition;
    std::vector<int> received_a;
    std::vector<int> received_b;

    {
        AsyncChannelOf<int> a("test.asyncof.broadcast", [&](int value) {
            {
                std::lock_guard<std::mutex> lock(mutex);
                received_a.push_back(value);
            }
            condition.notify_one();
        }, nullptr);

        AsyncChannelOf<int> b("test.asyncof.broadcast", [&](int value) {
            {
                std::lock_guard<std::mutex> lock(mutex);
                received_b.push_back(value);
            }
            condition.notify_one();
        }, nullptr);

        Channel::post("test.asyncof.broadcast", 7);

        std::unique_lock<std::mutex> lock(mutex);
        const bool delivered = condition.wait_for(lock, 500ms, [&] {
            return received_a.size() == 1 && received_b.size() == 1;
        });
        assert(delivered);
        assert(received_a[0] == 7);
        assert(received_b[0] == 7);
    }

    std::cout << "PASSED\n";
}

void test_unexpected_message_type_logging() {
    std::cout << "-> Testing unexpected message type logging... " << std::flush;

    std::mutex mutex;
    std::condition_variable condition;
    std::vector<std::string> logged;

    {
        AsyncChannelOf<int> ch("test.asyncof.unexpected", [](int) {}, [&](std::string msg) {
            {
                std::lock_guard<std::mutex> lock(mutex);
                logged.push_back(std::move(msg));
            }
            condition.notify_one();
        });

        // Post a message of unexpected type (std::string instead of int)
        Channel::post("test.asyncof.unexpected", std::string("mismatch"));

        std::unique_lock<std::mutex> lock(mutex);
        const bool logged_ok = condition.wait_for(lock, 500ms, [&] {
            return !logged.empty();
        });
        assert(logged_ok);
        assert(logged[0].find("Message was of unexpected type") != std::string::npos);
    }

    std::cout << "PASSED\n";
}

void test_stop_and_idempotence() {
    std::cout << "-> Testing stop and repeated stop... " << std::flush;

    std::atomic<int> callback_count{0};

    AsyncChannelOf<int> ch("test.asyncof.stop", [&](int) {
        ++callback_count;
    }, nullptr);

    Channel::post("test.asyncof.stop", 1);
    const bool delivered = wait_until([&] {
        return callback_count.load() == 1;
    });
    assert(delivered);

    ch.stop();
    ch.stop();

    Channel::post("test.asyncof.stop", 2);
    std::this_thread::sleep_for(20ms);
    assert(callback_count.load() == 1);

    std::cout << "PASSED\n";
}

void test_destructor_stops_worker() {
    std::cout << "-> Testing destructor stops worker thread... " << std::flush;

    std::atomic<bool> callback_called{false};

    {
        AsyncChannelOf<int> ch("test.asyncof.destructor", [&](int) {
            callback_called = true;
        }, nullptr);
        Channel::post("test.asyncof.destructor", 1);
        assert(wait_until([&] {
            return callback_called.load();
        }));
    }

    std::cout << "PASSED\n";
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

int main() {
    std::cout << "    Running AsyncChannelOf Test Suite\n";

    test_construction_and_callback();
    test_multiple_messages_fifo_order();
    test_broadcast_to_multiple_async_channels();
    test_unexpected_message_type_logging();
    test_stop_and_idempotence();
    test_destructor_stops_worker();

    std::cout << " All AsyncChannelOf Tests Completed\n";

    return 0;
}
