#pragma once

#include <functional>
#include <memory>
#include <string>

#include "AsyncChannel.hpp"

namespace msg {

template<typename Type>
class AsyncChannelOf : public AsyncChannel {
public:
    using TypeCallback = std::function<void(Type)>;

    AsyncChannelOf(const std::string &name, TypeCallback callback,
                        AsyncChannel::Logger logger);
    ~AsyncChannelOf();
private:
    TypeCallback m_type_callback;

    void _handle(IMessage::Ptr message);
};

template<typename Type>
AsyncChannelOf<Type>::AsyncChannelOf(const std::string &name, TypeCallback callback,
                                    AsyncChannel::Logger logger)
    : AsyncChannel(name, [this](IMessage::Ptr message){_handle(message);}, logger)
    , m_type_callback(callback)  {}

template<typename Type>
AsyncChannelOf<Type>::~AsyncChannelOf() {
    stop();
}

template<typename Type>
inline void AsyncChannelOf<Type>::_handle(IMessage::Ptr message) {
    if (nullptr == message) {
        m_logger("We got a null message");
        return;
    }

    if (auto * typed = dynamic_cast<Message<Type>*>(message.get())) {
        m_type_callback(typed->value());
        return;
    }

    m_logger("Message was of unexpected type:" + message->to_string());
}

} // msg
