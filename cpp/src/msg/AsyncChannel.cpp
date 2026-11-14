#include "AsyncChannel.hpp"

#include <exception>

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
            } catch (const std::exception& error) {
                m_logger(std::string("AsyncChannel call error: ") + error.what());
            }
        }
    });
}

AsyncChannel::~AsyncChannel() {
    // Ensure thread is stopped before destruction. A worker that cannot be
    // stopped would outlive this object, so then nothing is safe but to end
    // the program, as std::thread's destructor would.
    try {
        stop();
    } catch (...) {
        std::terminate();
    }
}

void AsyncChannel::stop() {
    if (!m_running) {
        return;
    }

    // signal thread to stop and then send an empty message to wake it up
    m_running = false;
    m_message_queue.push(IMessage::Ptr());

    if (m_thread.joinable()) {
        m_thread.join(); // Wait for thread to finish
    }
}

} // msg
