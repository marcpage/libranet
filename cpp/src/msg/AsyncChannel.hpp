#pragma once

#include <atomic>
#include <functional>
#include <string>
#include <thread>
#include <vector>

#include "Channel.hpp"
#include "IMessage.hpp"

namespace msg {

class AsyncChannel : public Channel {
public:
    using Callback = std::function<void(IMessage::Ptr)>;
    using Logger = std::function<void(std::string)>;

    AsyncChannel(const std::string& name, Callback callback, Logger logger = nullptr);
    AsyncChannel(std::vector<std::string> names, Callback callback, Logger logger = nullptr);
    ~AsyncChannel();

    void stop();

    AsyncChannel(const AsyncChannel&) = delete;
    AsyncChannel& operator=(const AsyncChannel&) = delete;
    AsyncChannel(AsyncChannel&&) = delete;
    AsyncChannel& operator=(AsyncChannel&&) = delete;

protected:
    // For errors when running asynchronously, in AsyncChannelOf's too.
    // NOLINTNEXTLINE(*-non-private-member-variables-in-classes)
    Logger m_logger;

private:
    std::thread m_thread;
    std::atomic<bool> m_running;
    Callback m_callback;
};

} // namespace msg
