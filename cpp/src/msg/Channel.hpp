#pragma once

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

#include "IMessage.hpp"
#include "Message.hpp"
#include "types/BlockingQueue.hpp"

namespace msg {

class Channel {
public:
    using Milliseconds = std::chrono::milliseconds;
    static constexpr Milliseconds wait_forever{-1};

    explicit Channel(const std::string& name);
    explicit Channel(std::vector<std::string> names);
    ~Channel();

    [[nodiscard]] bool empty() const;
    [[nodiscard]] std::size_t size() const;

    [[nodiscard]] std::optional<IMessage::Ptr> next_message(Milliseconds timeout = wait_forever);

    template<typename Type>
    [[nodiscard]] std::optional<Type> next(Milliseconds timeout = wait_forever);

    static void post_message(const std::string& name, const IMessage::Ptr& message);

    template<typename Type>
    static void post(const std::string& name, Type value);

    Channel(const Channel&) = delete;
    Channel& operator=(const Channel&) = delete;
    Channel(Channel&&) = delete;
    Channel& operator=(Channel&&) = delete;

protected:
    // AsyncChannel's worker takes messages from it, and is woken through it.
    // NOLINTNEXTLINE(*-non-private-member-variables-in-classes)
    types::BlockingQueue<IMessage::Ptr> m_message_queue;

private:
    std::vector<std::string> m_names; // Each once, so that each is left once
    static std::map<std::string, std::vector<Channel*>> m_channels;
    static std::mutex m_channel_lock;
};

template<typename Type>
std::optional<Type> Channel::next(Milliseconds timeout) {
    auto message = next_message(timeout);

    if (!message) {
        return std::nullopt;
    }

    const auto* typed = dynamic_cast<Message<Type>*>(message->get());

    if (nullptr == typed) {
        return std::nullopt; // type mismatch
    }

    return typed->value();
}

template<typename Type>
void Channel::post(const std::string& name, Type value) {
    post_message(name, std::make_shared<Message<Type>>(std::move(value)));
}

} // namespace msg
