#include <gtest/gtest.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "msg/AsyncChannel.hpp"
#include "msg/Channel.hpp"
#include "msg/IMessage.hpp"

using namespace msg;
using namespace std::chrono_literals;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// How long a test waits for its messages to be handled.
constexpr auto handling_timeout = 500ms;

template<typename Predicate>
static bool wait_until(Predicate predicate, std::chrono::milliseconds timeout = handling_timeout) {
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
        const AsyncChannel ch("test.async.basic", [&](const IMessage::Ptr& message) {
            {
                const std::scoped_lock lock(mutex);
                received.push_back(message->to_string());
            }
            condition.notify_one();
        });

        EXPECT_TRUE(ch.empty());

        constexpr auto posted_value = 42;
        Channel::post("test.async.basic", posted_value);

        std::unique_lock<std::mutex> lock(mutex);
        const bool delivered = condition.wait_for(lock, handling_timeout, [&] {
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
        const AsyncChannel ch("test.async.fifo", [&](const IMessage::Ptr& message) {
            {
                const std::scoped_lock lock(mutex);
                received.push_back(message->to_string());
            }
            condition.notify_one();
        });

        constexpr auto first_value = 10;
        constexpr auto second_value = 20;
        constexpr auto third_value = 30;
        Channel::post("test.async.fifo", first_value);
        Channel::post("test.async.fifo", second_value);
        Channel::post("test.async.fifo", third_value);

        std::unique_lock<std::mutex> lock(mutex);
        const bool delivered = condition.wait_for(lock, handling_timeout, [&] {
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
        const AsyncChannel a("test.async.broadcast", [&](const IMessage::Ptr& message) {
            {
                const std::scoped_lock lock(mutex);
                received_a.push_back(std::stoi(message->to_string()));
            }
            condition.notify_one();
        });

        const AsyncChannel b("test.async.broadcast", [&](const IMessage::Ptr& message) {
            {
                const std::scoped_lock lock(mutex);
                received_b.push_back(std::stoi(message->to_string()));
            }
            condition.notify_one();
        });

        constexpr auto posted_value = 7;
        Channel::post("test.async.broadcast", posted_value);

        std::unique_lock<std::mutex> lock(mutex);
        const bool delivered = condition.wait_for(lock, handling_timeout, [&] {
            return received_a.size() == 1 && received_b.size() == 1;
        });
        ASSERT_TRUE(delivered);
        EXPECT_EQ(received_a[0], posted_value);
        EXPECT_EQ(received_b[0], posted_value);
    }
}

TEST(AsyncChannelTest, CallbackExceptionDoesNotStopProcessing) {
    std::mutex mutex;
    std::condition_variable condition;
    std::vector<std::string> received;
    std::vector<std::string> logged;

    {
        const AsyncChannel ch("test.async.exceptions", [&](const IMessage::Ptr& message) {
            const std::string value = message->to_string();
            if (value == "1") {
                throw std::runtime_error("expected test exception");
            }
            {
                const std::scoped_lock lock(mutex);
                received.push_back(value);
            }
            condition.notify_one();
        }, [&](std::string message) {
            {
                const std::scoped_lock lock(mutex);
                logged.push_back(std::move(message));
            }
            condition.notify_one();
        });

        Channel::post("test.async.exceptions", 1);
        Channel::post("test.async.exceptions", 2);

        std::unique_lock<std::mutex> lock(mutex);
        const bool processed = condition.wait_for(lock, handling_timeout, [&] {
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

    AsyncChannel ch("test.async.stop", [&](const IMessage::Ptr&) {
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
    constexpr auto delivery_wait = 20ms;
    std::this_thread::sleep_for(delivery_wait);
    EXPECT_EQ(callback_count.load(), 1);
}

TEST(AsyncChannelTest, DestructorStopsWorker) {
    std::atomic<bool> callback_called{false};

    {
        const AsyncChannel ch("test.async.destructor", [&](const IMessage::Ptr&) {
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
