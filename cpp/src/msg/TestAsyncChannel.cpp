#include <gtest/gtest.h>
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

template<typename Predicate>
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

TEST(AsyncChannelTest, ConstructionAndCallback) {
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

        EXPECT_TRUE(ch.empty());

        Channel::post("test.async.basic", 42);

        std::unique_lock<std::mutex> lock(mutex);
        const bool delivered = condition.wait_for(lock, 500ms, [&] {
            return received.size() == 1;
        });
        ASSERT_TRUE(delivered);
        EXPECT_EQ(received[0], "42");
    }
}

TEST(AsyncChannelTest, MultipleMessagesFifoOrder) {
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
        ASSERT_TRUE(delivered);
        EXPECT_EQ(received[0], "10");
        EXPECT_EQ(received[1], "20");
        EXPECT_EQ(received[2], "30");
    }
}

TEST(AsyncChannelTest, BroadcastToMultipleAsyncChannels) {
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
        ASSERT_TRUE(delivered);
        EXPECT_EQ(received_a[0], 7);
        EXPECT_EQ(received_b[0], 7);
    }
}

TEST(AsyncChannelTest, CallbackExceptionDoesNotStopProcessing) {
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
        ASSERT_TRUE(processed);
        EXPECT_EQ(received.size(), 1U);
        EXPECT_EQ(received[0], "2");
        EXPECT_EQ(logged.size(), 1U);
    }
}

TEST(AsyncChannelTest, StopAndIdempotence) {
    std::atomic<int> callback_count{0};

    AsyncChannel ch("test.async.stop", [&](IMessage::Ptr) {
        ++callback_count;
    });

    Channel::post("test.async.stop", 1);
    const bool delivered = wait_until([&] {
        return callback_count.load() == 1;
    });
    EXPECT_TRUE(delivered);

    ch.stop();
    ch.stop();

    Channel::post("test.async.stop", 2);
    std::this_thread::sleep_for(20ms);
    EXPECT_EQ(callback_count.load(), 1);
}

TEST(AsyncChannelTest, DestructorStopsWorker) {
    std::atomic<bool> callback_called{false};

    {
        AsyncChannel ch("test.async.destructor", [&](IMessage::Ptr) {
            callback_called = true;
        });
        Channel::post("test.async.destructor", 1);
        EXPECT_TRUE(wait_until([&] {
            return callback_called.load();
        }));
    }
}

TEST(AsyncChannelTest, NullCallbackRejected) {
    EXPECT_THROW(AsyncChannel("test.async.null", nullptr), std::invalid_argument);
}
