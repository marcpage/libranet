#include "AsyncChannel.hpp"

#include <exception>
#include <stdexcept>
#include <string>
#include <vector>

#include "Channel.hpp"
#include "IMessage.hpp"


using std::exception;
using std::invalid_argument;
using std::string;
using std::terminate;
using std::thread;
using std::vector;

namespace {
void noop(const string& /*unused*/) {}
} // namespace

namespace msg {

AsyncChannel::AsyncChannel(const string& name, const Callback& callback, const Logger& logger)
    : AsyncChannel(vector<string>{name}, callback, logger) {}

AsyncChannel::AsyncChannel(vector<string> names, const Callback& callback, const Logger& logger)
    : Channel(std::move(names))
    , m_logger(nullptr == logger ? noop : logger)
    , m_running(true)
    , m_callback(callback) {

    if (nullptr == callback) {
        throw invalid_argument("You must pass a callback");
    }

    m_thread = thread([this] {
        while (m_running) {
            auto message = next_message();

            if (!m_running || nullptr == message || !message.has_value()) {
                continue;
            }

            try {
                m_callback(message.value());
            } catch (const exception& error) {
                m_logger(string("AsyncChannel call error: ") + error.what());
            }
        }
    });
}

AsyncChannel::~AsyncChannel() {
    // Ensure thread is stopped before destruction. A worker that cannot be
    // stopped would outlive this object, so then nothing is safe but to end
    // the program, as thread's destructor would.
    try {
        stop();
    } catch (...) {
        terminate();
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

} // namespace msg
