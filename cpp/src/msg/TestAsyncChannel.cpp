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

#include "AsyncChannel.hpp"
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
    std::cout << "-> Testing construction and asynchronous callback... " << std::flush;

    std::mutex mutex;
    std::condition_variable condition;
    std::vector<std::string> received;

    {
        AsyncChannel ch("test.async.basic", [&](IMessage::Ptr message) {
            {
                std::lock_guard<std::mutex> lock(mutex);
                received.push_back(message->to_string());
            }
            condition.notify_one();
        });

        assert(ch.empty());

        Channel::post("test.async.basic", 42);

        std::unique_lock<std::mutex> lock(mutex);
        const bool delivered = condition.wait_for(lock, 500ms, [&] {
            return received.size() == 1;
        });
        assert(delivered);
        assert(received[0] == "42");
    }

    std::cout << "PASSED\n";
}

void test_multiple_messages_fifo_order() {
    std::cout << "-> Testing asynchronous FIFO delivery... " << std::flush;

    std::mutex mutex;
    std::condition_variable condition;
    std::vector<std::string> received;

    {
        AsyncChannel ch("test.async.fifo", [&](IMessage::Ptr message) {
            {
                std::lock_guard<std::mutex> lock(mutex);
                received.push_back(message->to_string());
            }
            condition.notify_one();
        });

        Channel::post("test.async.fifo", 10);
        Channel::post("test.async.fifo", 20);
        Channel::post("test.async.fifo", 30);

        std::unique_lock<std::mutex> lock(mutex);
        const bool delivered = condition.wait_for(lock, 500ms, [&] {
            return received.size() == 3;
        });
        assert(delivered);
        assert(received[0] == "10");
        assert(received[1] == "20");
        assert(received[2] == "30");
    }

    std::cout << "PASSED\n";
}

void test_broadcast_to_multiple_async_channels() {
    std::cout << "-> Testing broadcast to multiple async channels... " << std::flush;

    std::mutex mutex;
    std::condition_variable condition;
    std::vector<int> received_a;
    std::vector<int> received_b;

    {
        AsyncChannel a("test.async.broadcast", [&](IMessage::Ptr message) {
            {
                std::lock_guard<std::mutex> lock(mutex);
                received_a.push_back(std::stoi(message->to_string()));
            }
            condition.notify_one();
        });

        AsyncChannel b("test.async.broadcast", [&](IMessage::Ptr message) {
            {
                std::lock_guard<std::mutex> lock(mutex);
                received_b.push_back(std::stoi(message->to_string()));
            }
            condition.notify_one();
        });

        Channel::post("test.async.broadcast", 7);

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

void test_callback_exception_does_not_stop_processing() {
    std::cout << "-> Testing callback exception handling... " << std::flush;

    std::mutex mutex;
    std::condition_variable condition;
    std::vector<std::string> received;
    std::vector<std::string> logged;

    {
        AsyncChannel ch("test.async.exceptions", [&](IMessage::Ptr message) {
            const std::string value = message->to_string();
            if (value == "1") {
                throw std::runtime_error("expected test exception");
            }
            {
                std::lock_guard<std::mutex> lock(mutex);
                received.push_back(value);
            }
            condition.notify_one();
        }, [&](std::string message) {
            {
                std::lock_guard<std::mutex> lock(mutex);
                logged.push_back(std::move(message));
            }
            condition.notify_one();
        });

        Channel::post("test.async.exceptions", 1);
        Channel::post("test.async.exceptions", 2);

        std::unique_lock<std::mutex> lock(mutex);
        const bool processed = condition.wait_for(lock, 500ms, [&] {
            return !received.empty() && !logged.empty();
        });
        assert(processed);
        assert(received.size() == 1);
        assert(received[0] == "2");
        assert(logged.size() == 1);
    }

    std::cout << "PASSED\n";
}

void test_stop_and_idempotence() {
    std::cout << "-> Testing stop and repeated stop... " << std::flush;

    std::atomic<int> callback_count{0};

    AsyncChannel ch("test.async.stop", [&](IMessage::Ptr) {
        ++callback_count;
    });

    Channel::post("test.async.stop", 1);
    const bool delivered = wait_until([&] {
        return callback_count.load() == 1;
    });
    assert(delivered);

    ch.stop();
    ch.stop();

    Channel::post("test.async.stop", 2);
    std::this_thread::sleep_for(20ms);
    assert(callback_count.load() == 1);

    std::cout << "PASSED\n";
}

void test_destructor_stops_worker() {
    std::cout << "-> Testing destructor stops worker thread... " << std::flush;

    std::atomic<bool> callback_called{false};

    {
        AsyncChannel ch("test.async.destructor", [&](IMessage::Ptr) {
            callback_called = true;
        });
        Channel::post("test.async.destructor", 1);
        assert(wait_until([&] {
            return callback_called.load();
        }));
    }

    std::cout << "PASSED\n";
}

void test_null_callback_rejected() {
    std::cout << "-> Testing null callback is rejected... " << std::flush;

    bool threw = false;
    try {
        AsyncChannel ch("test.async.null", nullptr);
    } catch (const std::invalid_argument&) {
        threw = true;
    }
    assert(threw);

    std::cout << "PASSED\n";
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

int main() {
    std::cout << "    Running AsyncChannel Test Suite\n";

    test_construction_and_callback();
    test_multiple_messages_fifo_order();
    test_broadcast_to_multiple_async_channels();
    test_callback_exception_does_not_stop_processing();
    test_stop_and_idempotence();
    test_destructor_stops_worker();
    test_null_callback_rejected();

    std::cout << " All AsyncChannel Tests Completed\n";

    return 0;
}
