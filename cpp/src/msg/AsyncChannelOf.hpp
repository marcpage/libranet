#pragma once

#include <functional>
#include <memory>
#include <string>
#include <utility>

#include "AsyncChannel.hpp"

namespace msg {

template<typename Type>
class AsyncChannelOf : public AsyncChannel {
public:
    using TypeCallback = std::function<void(Type)>;

    AsyncChannelOf(const std::string &name, TypeCallback callback,
                   const AsyncChannel::Logger &logger);
    ~AsyncChannelOf();
private:
    TypeCallback m_type_callback;

    void _handle(const IMessage::Ptr &message);
};

template<typename Type>
AsyncChannelOf<Type>::AsyncChannelOf(const std::string &name, TypeCallback callback,
                                     const AsyncChannel::Logger &logger)
    : AsyncChannel(name, [this](const IMessage::Ptr &message) { _handle(message); }, logger)
    , m_type_callback(std::move(callback)) {}

template<typename Type>
AsyncChannelOf<Type>::~AsyncChannelOf() {
    stop();
}

template<typename Type>
inline void AsyncChannelOf<Type>::_handle(const IMessage::Ptr &message) {
    if (nullptr == message) {
        m_logger("We got a null message");
        return;
    }

    if (const auto *typed = dynamic_cast<Message<Type> *>(message.get())) {
        m_type_callback(typed->value());
        return;
    }

    m_logger("Message was of unexpected type:" + message->to_string());
}

} // namespace msg
