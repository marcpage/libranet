#include <gtest/gtest.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "msg/AsyncChannelOf.hpp"
#include "msg/Channel.hpp"

using msg::AsyncChannelOf;
using msg::Channel;
using namespace std::chrono_literals;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

namespace {

// How long a test waits for its messages to be handled.
constexpr auto handling_timeout = 500ms;

template<typename Predicate>
bool wait_until(Predicate predicate, std::chrono::milliseconds timeout = handling_timeout) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        if (predicate()) {
            return true;
        }
        std::this_thread::sleep_for(1ms);
    }
    return predicate();
}

} // namespace

// ---------------------------------------------------------------------------
// Test cases
// ---------------------------------------------------------------------------

TEST(AsyncChannelOfTest, ConstructionAndCallback) {
    std::mutex mutex;
    std::condition_variable condition;
    std::vector<int> received;

    {
        const AsyncChannelOf<int> ch("test.asyncof.basic", [&](int value) {
            {
                const std::scoped_lock lock(mutex);
                received.push_back(value);
            }
            condition.notify_one();
        }, nullptr);

        EXPECT_TRUE(ch.empty());

        constexpr auto posted_value = 42;
        Channel::post("test.asyncof.basic", posted_value);

        std::unique_lock<std::mutex> lock(mutex);
        const bool delivered = condition.wait_for(lock, handling_timeout, [&] {
            return received.size() == 1;
        });
        ASSERT_TRUE(delivered);
        EXPECT_EQ(received[0], posted_value);
    }
}

TEST(AsyncChannelOfTest, MultipleMessagesFifoOrder) {
    std::mutex mutex;
    std::condition_variable condition;
    std::vector<int> received;

    {
        const AsyncChannelOf<int> ch("test.asyncof.fifo", [&](int value) {
            {
                const std::scoped_lock lock(mutex);
                received.push_back(value);
            }
            condition.notify_one();
        }, nullptr);

        constexpr auto first_value = 10;
        constexpr auto second_value = 20;
        constexpr auto third_value = 30;
        Channel::post("test.asyncof.fifo", first_value);
        Channel::post("test.asyncof.fifo", second_value);
        Channel::post("test.asyncof.fifo", third_value);

        std::unique_lock<std::mutex> lock(mutex);
        const bool delivered = condition.wait_for(lock, handling_timeout, [&] {
            return received.size() == 3;
        });
        ASSERT_TRUE(delivered);
        EXPECT_EQ(received[0], first_value);
        EXPECT_EQ(received[1], second_value);
        EXPECT_EQ(received[2], third_value);
    }
}

TEST(AsyncChannelOfTest, BroadcastToMultipleAsyncChannels) {
    std::mutex mutex;
    std::condition_variable condition;
    std::vector<int> received_a;
    std::vector<int> received_b;

    {
        const AsyncChannelOf<int> a("test.asyncof.broadcast", [&](int value) {
            {
                const std::scoped_lock lock(mutex);
                received_a.push_back(value);
            }
            condition.notify_one();
        }, nullptr);

        const AsyncChannelOf<int> b("test.asyncof.broadcast", [&](int value) {
            {
                const std::scoped_lock lock(mutex);
                received_b.push_back(value);
            }
            condition.notify_one();
        }, nullptr);

        constexpr auto posted_value = 7;
        Channel::post("test.asyncof.broadcast", posted_value);

        std::unique_lock<std::mutex> lock(mutex);
        const bool delivered = condition.wait_for(lock, handling_timeout, [&] {
            return received_a.size() == 1 && received_b.size() == 1;
        });
        ASSERT_TRUE(delivered);
        EXPECT_EQ(received_a[0], posted_value);
        EXPECT_EQ(received_b[0], posted_value);
    }
}

TEST(AsyncChannelOfTest, UnexpectedMessageTypeLogging) {
    std::mutex mutex;
    std::condition_variable condition;
    std::vector<std::string> logged;

    {
        const AsyncChannelOf<int> ch("test.asyncof.unexpected", [](int) {}, [&](std::string msg) {
            {
                const std::scoped_lock lock(mutex);
                logged.push_back(std::move(msg));
            }
            condition.notify_one();
        });

        // Post a message of unexpected type (std::string instead of int)
        Channel::post("test.asyncof.unexpected", std::string("mismatch"));

        std::unique_lock<std::mutex> lock(mutex);
        const bool logged_ok = condition.wait_for(lock, handling_timeout, [&] {
            return !logged.empty();
        });
        ASSERT_TRUE(logged_ok);
        EXPECT_NE(logged[0].find("Message was of unexpected type"), std::string::npos);
    }
}

TEST(AsyncChannelOfTest, StopAndIdempotence) {
    std::atomic<int> callback_count{0};

    AsyncChannelOf<int> ch("test.asyncof.stop", [&](int) {
        ++callback_count;
    }, nullptr);

    Channel::post("test.asyncof.stop", 1);
    const bool delivered = wait_until([&] {
        return callback_count.load() == 1;
    });
    EXPECT_TRUE(delivered);

    ch.stop();
    ch.stop();

    Channel::post("test.asyncof.stop", 2);
    constexpr auto delivery_wait = 20ms;
    std::this_thread::sleep_for(delivery_wait);
    EXPECT_EQ(callback_count.load(), 1);
}

TEST(AsyncChannelOfTest, DestructorStopsWorker) {
    std::atomic<bool> callback_called{false};

    {
        const AsyncChannelOf<int> ch("test.asyncof.destructor", [&](int) {
            callback_called = true;
        }, nullptr);
        Channel::post("test.asyncof.destructor", 1);
        EXPECT_TRUE(wait_until([&] {
            return callback_called.load();
        }));
    }
}
