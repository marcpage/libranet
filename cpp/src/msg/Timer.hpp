#pragma once

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <exception>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>

#include "Channel.hpp"

namespace msg {

// Posts a tick under a name every interval, from a thread of its own, until
// stopped.
class Timer {
public:
    using Milliseconds = std::chrono::milliseconds;

    // name tells apart the timers a channel listens to; count is from 1. A
    // plain struct, but for its conversion to text.
    struct Tick {
        // NOLINTBEGIN(misc-non-private-member-variables-in-classes)
        std::string name;
        std::uint64_t count{0};
        // NOLINTEND(misc-non-private-member-variables-in-classes)

        explicit operator std::string() const;
    };

    Timer(std::string name, Milliseconds interval);
    ~Timer();

    void stop();

    Timer(const Timer&) = delete;
    Timer& operator=(const Timer&) = delete;
    Timer(Timer&&) = delete;
    Timer& operator=(Timer&&) = delete;

private:
    std::mutex m_mutex;
    std::condition_variable m_wake; // Wakes the thread to stop
    bool m_running{true};           // Guarded by m_mutex
    std::thread m_thread;
};


inline Timer::Tick::operator std::string() const {
    return name + " tick " + std::to_string(count);
}

inline Timer::Timer(std::string name, Milliseconds interval) {
    if (interval <= Milliseconds::zero()) {
        throw std::invalid_argument("A timer's interval must be more than zero");
    }

    m_thread = std::thread([this, name = std::move(name), interval] {
        using Clock = std::chrono::steady_clock;
        auto due = Clock::now() + interval;
        std::uint64_t count = 0;
        std::unique_lock lock(m_mutex);

        // Each tick is due an interval after the one before, so the ticks
        // do not drift. A tick that came too late to be posted in time is
        // let go, rather than posted in a burst with the ones after it.
        while (!m_wake.wait_until(lock, due, [this] { return !m_running; })) {
            lock.unlock();
            ++count;
            Channel::post(name, Tick{name, count});
            due += interval;

            if (const auto now = Clock::now(); due <= now) {
                due = now + interval;
            }

            lock.lock();
        }
    });
}

inline Timer::~Timer() {
    // A thread that cannot be stopped would outlive this object, so then
    // nothing is safe but to end the program, as std::thread's destructor
    // would.
    try {
        stop();
    } catch (...) {
        std::terminate();
    }
}

inline void Timer::stop() {
    {
        const std::scoped_lock lock(m_mutex);
        m_running = false;
    }

    m_wake.notify_one();

    if (m_thread.joinable()) {
        m_thread.join();
    }
}

} // namespace msg
