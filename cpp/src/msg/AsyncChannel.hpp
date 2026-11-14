#pragma once

#include <atomic>
#include <functional>
#include <string>
#include <thread>

#include "Channel.hpp"

namespace msg {

class AsyncChannel : public Channel {
public:
    using Callback = std::function<void(IMessage::Ptr)>;
    using Logger = std::function<void(std::string)>;

    AsyncChannel(const std::string& name, const Callback& callback, const Logger& logger = nullptr);
    ~AsyncChannel();
    void stop();

protected:
    Logger m_logger; // for errors when running asynchronously

private:
    std::thread m_thread;        // Thread for processing messages
    std::atomic<bool> m_running; // Flag to control thread execution
    Callback m_callback;         // Callback for messages

    AsyncChannel(const AsyncChannel&) = delete;
    AsyncChannel& operator=(const AsyncChannel&) = delete;
    AsyncChannel(AsyncChannel&&) = delete;
    AsyncChannel& operator=(AsyncChannel&&) = delete;
};

} // msg
