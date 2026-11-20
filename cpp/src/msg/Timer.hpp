#pragma once

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>

namespace msg {

// Posts a tick under a name every interval, from a thread of its own, until
// stopped. Stopping does not wait for the end of the interval in hand.
class Timer {
public:
    using Milliseconds = std::chrono::milliseconds;

    // What the timer posts: the name it posts under, so that a channel
    // listening to several timers can tell them apart, and which tick it
    // is, counting from 1. An event is a plain struct; this one converts to
    // a string so that Message can say what it holds.
    struct Tick {
        // NOLINTBEGIN(misc-non-private-member-variables-in-classes)
        std::string name;
        std::uint64_t count{0};
        // NOLINTEND(misc-non-private-member-variables-in-classes)

        explicit operator std::string() const;
    };

    Timer(const std::string& name, Milliseconds interval);
    ~Timer();
    // Its thread holds this object, so it is neither copied nor moved.
    Timer(const Timer&) = delete;
    Timer& operator=(const Timer&) = delete;
    Timer(Timer&&) = delete;
    Timer& operator=(Timer&&) = delete;
    void stop();

private:
    std::mutex m_mutex;
    std::condition_variable m_wake; // Wakes the thread to stop
    bool m_running{true};           // Guarded by m_mutex
    std::thread m_thread;
};

} // namespace msg
