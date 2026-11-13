#include "AsyncChannel.hpp"

namespace {
    void noop(std::string) {}
}

namespace msg {

AsyncChannel::AsyncChannel(const std::string& name, Callback callback, Logger logger)
    : Channel(name)
    , m_logger(nullptr == logger ? noop : logger)
    , m_running(true)
    , m_callback(callback) {

    if (nullptr == callback) {
        throw std::invalid_argument("You must pass a callback");
    }

    m_thread = std::thread([this]() {
        while (m_running) {
            auto message = next_message();

            if (!m_running || nullptr == message || !message.has_value()) {
                continue;
            }

            try {
                m_callback(message.value());
            } catch(const std::exception &error) {
                m_logger(std::string("AsyncChannel call error: ") + error.what());
            }
        }
    });
}

AsyncChannel::~AsyncChannel() {
    stop(); // Ensure thread is stopped before destruction
}

void AsyncChannel::stop() {
    if (!m_running) {
        return;
    }

    auto * message = new Message<std::string>("");

    // signal thread to stop and then send a message to wake it up
    m_running = false;
    m_message_queue.push(IMessage::Ptr(message));

    if (m_thread.joinable()) {
        m_thread.join(); // Wait for thread to finish
    }
}

} // msg
