#include <gtest/gtest.h>
#include <chrono>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>

#include "msg/Channel.hpp"
#include "msg/Message.hpp"
#include "msg/Timer.hpp"

using msg::Channel;
using msg::Message;
using msg::Timer;
using namespace std::chrono_literals;

namespace {

// How long a test waits for a tick. A wait ends as soon as one comes, so
// only a failing test waits this long; it is generous because a sanitizer
// build, sharing a slow machine with other tests, can take far longer than
// a test run alone.
constexpr auto tick_timeout = 5s;

// An interval no test runs long enough to see the end of.
constexpr auto never = std::chrono::hours(1);

// How long a test watches for a tick that should not come.
constexpr auto quiet_time = 20ms;

} // namespace

// The tests read optionals after ASSERT_TRUE(x.has_value()), which this
// check does not follow.
// NOLINTBEGIN(bugprone-unchecked-optional-access)

TEST(TimerTest, TicksInOrder) {
    Channel channel("test.timer.ticks");
    const Timer timer("test.timer.ticks", 1ms);

    for (std::uint64_t expected = 1; expected <= 3; ++expected) {
        const auto tick = channel.next<Timer::Tick>(tick_timeout);
        ASSERT_TRUE(tick.has_value());
        EXPECT_EQ(tick->name, "test.timer.ticks");
        EXPECT_EQ(tick->count, expected);
    }
}

TEST(TimerTest, TicksAnIntervalApart) {
    constexpr auto interval = 20ms;
    constexpr auto ticks = 3;
    Channel channel("test.timer.apart");
    const auto start = std::chrono::steady_clock::now();
    const Timer timer("test.timer.apart", interval);

    for (auto tick = 0; tick < ticks; ++tick) {
        ASSERT_TRUE(channel.next<Timer::Tick>(tick_timeout).has_value());
    }

    EXPECT_GE(std::chrono::steady_clock::now() - start, ticks * interval);
}

TEST(TimerTest, NoTickBeforeTheInterval) {
    const Channel channel("test.timer.early");
    const Timer timer("test.timer.early", never);

    std::this_thread::sleep_for(quiet_time);
    EXPECT_TRUE(channel.empty());
}

TEST(TimerTest, PostsOnlyUnderItsName) {
    Channel channel("test.timer.mine");
    const Channel other("test.timer.other");
    const Timer timer("test.timer.mine", 1ms);

    ASSERT_TRUE(channel.next<Timer::Tick>(tick_timeout).has_value());
    EXPECT_TRUE(other.empty());
}

TEST(TimerTest, StopsAtOnce) {
    Timer timer("test.timer.stop", never);
    const auto start = std::chrono::steady_clock::now();

    timer.stop();
    EXPECT_LT(std::chrono::steady_clock::now() - start, tick_timeout);
}

TEST(TimerTest, NoTicksOnceStopped) {
    Channel channel("test.timer.stopped");
    Timer timer("test.timer.stopped", 1ms);

    ASSERT_TRUE(channel.next<Timer::Tick>(tick_timeout).has_value());
    timer.stop();

    while (!channel.empty()) {
        static_cast<void>(channel.next_message());
    }

    std::this_thread::sleep_for(quiet_time);
    EXPECT_TRUE(channel.empty());
}

TEST(TimerTest, StopTwiceThenDestroy) {
    const auto timer = std::make_unique<Timer>("test.timer.twice", 1ms);

    timer->stop();
    timer->stop();
}

TEST(TimerTest, DestructorStops) {
    Channel channel("test.timer.destroyed");

    {
        const Timer timer("test.timer.destroyed", 1ms);
        ASSERT_TRUE(channel.next<Timer::Tick>(tick_timeout).has_value());
    }

    while (!channel.empty()) {
        static_cast<void>(channel.next_message());
    }

    std::this_thread::sleep_for(quiet_time);
    EXPECT_TRUE(channel.empty());
}

TEST(TimerTest, IntervalMustBePositive) {
    EXPECT_THROW((void)Timer("test.timer.zero", 0ms), std::invalid_argument);
    EXPECT_THROW((void)Timer("test.timer.negative", -1ms), std::invalid_argument);
}

TEST(TimerTest, TickToString) {
    const Message<Timer::Tick> message(Timer::Tick{"test.timer.string", 7});

    EXPECT_EQ(message.to_string(), "test.timer.string tick 7");
}

// NOLINTEND(bugprone-unchecked-optional-access)
