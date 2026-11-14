#pragma once

#include <algorithm>
#include <chrono>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include "IMessage.hpp"
#include "Message.hpp"
#include "types/BlockingQueue.hpp"

namespace msg {

class Channel {
public:
    using Milliseconds = std::chrono::milliseconds;
    static constexpr Milliseconds WaitForever{-1};

    explicit Channel(const std::string& name);
    ~Channel();
    [[nodiscard]] bool empty() const;
    [[nodiscard]] size_t size() const;

    [[nodiscard]] std::optional<IMessage::Ptr> next_message(Milliseconds timeout = WaitForever);

    template<typename Type>
    [[nodiscard]] std::optional<Type> next(Milliseconds timeout = WaitForever);

    static void post_message(const std::string& name, IMessage::Ptr message);

    template<typename Type>
    static void post(const std::string& name, Type value);

protected:
    types::BlockingQueue<IMessage::Ptr> m_message_queue;

private:
    Channel(const Channel&) = delete;
    Channel& operator=(const Channel&) = delete;
    Channel(Channel&&) = delete;
    Channel& operator=(Channel&&) = delete;

    static std::map<std::string, std::vector<Channel*>> m_channels;
    static std::mutex m_channel_lock;
};

template<typename Type>
std::optional<Type> Channel::next(Milliseconds timeout) {
    auto message = next_message(timeout);

    if (!message) {
        return std::nullopt;
    }

    auto* typed = dynamic_cast<Message<Type>*>(message->get());

    if (!typed) {
        return std::nullopt; // type mismatch
    }

    return typed->value();
}

template<typename Type>
void Channel::post(const std::string& name, Type value) {
    post_message(name, std::make_shared<Message<Type>>(std::move(value)));
}

} // namespace msg
