#include "DialOutcomes.hpp"

#include <cstddef>
#include <mutex>
#include <optional>
#include <random>
#include <string>

#include "logger/Logger.hpp"
#include "msg/IMessage.hpp"
#include "msg/Message.hpp"
#include "peer/Connections.hpp"
#include "peer/PeerConnection.hpp"
#include "types/Constants.hpp"

using config::DialOutcomes;
using msg::IMessage;
using msg::value_of;
using peer::PeerConnection;
using std::nullopt;
using std::optional;
using std::random_device;
using std::scoped_lock;
using std::size_t;
using std::string;
using std::uniform_int_distribution;
using std::unique_lock;
using types::hex_digits;
using AddressDialed = DialOutcomes::AddressDialed;
using Milliseconds = DialOutcomes::Milliseconds;

// The thread takes this event, which peer::Connections posts from another
// file, so nothing here would make its message's virtual to_string(), which
// value_of<>() casts to.
template class msg::Message<AddressDialed>;

namespace {

// Hex digits in a dial's id: 16 random bytes, as the Python node's has, so
// that no two requests share one.
constexpr size_t dial_id_digits = 32;

// A new dial's id, in hex.
string new_dial_id() {
    random_device random;
    uniform_int_distribution<size_t> digit(0, hex_digits.size() - 1);
    string dial_id;

    dial_id.reserve(dial_id_digits);

    while (dial_id.size() < dial_id_digits) {
        dial_id += hex_digits.at(digit(random));
    }

    return dial_id;
}

// Reports what the thread could not handle.
void log_error(const string& text) {
    lErr << text;
}

} // namespace

namespace config {

DialOutcomes::DialOutcomes(const PeerConnection::Limits& limits, Milliseconds report_time)
    : m_wait(limits.connect_timeout + (2 * limits.request_timeout) + report_time)
    , m_channel(AddressDialed::name, [this](const IMessage::Ptr& message) { _take(message); },
                log_error) {}

DialOutcomes::~DialOutcomes() {
    stop();

    unique_lock lock(m_mutex);

    m_changed.wait(lock, [this] { return m_awaited.empty(); });
}

Milliseconds DialOutcomes::wait() const noexcept {
    return m_wait;
}

optional<AddressDialed> DialOutcomes::await(const Ask& ask) {
    const auto dial_id = _expect();

    if (!dial_id) {
        return nullopt;
    }

    optional<AddressDialed> dialed;

    try {
        ask(*dial_id);
        dialed = _wait_for(*dial_id);
    } catch (...) {
        _forget(*dial_id);
        throw;
    }

    _forget(*dial_id);
    return dialed;
}

void DialOutcomes::stop() {
    const scoped_lock lock(m_mutex);

    m_stopped = true;
    m_changed.notify_all();
}

// A new dial's id, which a request waits on from now, or nothing once this
// is stopped.
optional<string> DialOutcomes::_expect() {
    const scoped_lock lock(m_mutex);

    if (m_stopped) {
        return nullopt;
    }

    auto dial_id = new_dial_id();

    m_awaited.emplace(dial_id, nullopt);
    return dial_id;
}

// What came of the dial dial_id names, once it comes, or nothing when it
// does not come in time or this is stopped first.
optional<AddressDialed> DialOutcomes::_wait_for(const string& dial_id) {
    unique_lock lock(m_mutex);
    const auto& awaited = m_awaited.at(dial_id);

    m_changed.wait_for(lock, m_wait, [this, &awaited] { return m_stopped || awaited.has_value(); });
    return awaited;
}

// No request waits on the dial dial_id names any longer.
void DialOutcomes::_forget(const string& dial_id) {
    const scoped_lock lock(m_mutex);

    m_awaited.erase(dial_id);
    // While locked, so that the destructor, which waits for this, cannot go
    // on to destroy what is notified before it is.
    m_changed.notify_all();
}

// Hands what came of a dial to the request waiting on it, if one still is.
void DialOutcomes::_take(const IMessage::Ptr& message) {
    const auto* const dialed = value_of<AddressDialed>(message);

    if (nullptr == dialed) {
        log_error("Not what came of a dial: " + message->to_string());
        return;
    }

    const scoped_lock lock(m_mutex);
    const auto awaited = m_awaited.find(dialed->dial_id);

    if (m_awaited.end() == awaited) {
        lInfo << "No request waits any longer on" << string(*dialed);
        return;
    }

    awaited->second = *dialed;
    m_changed.notify_all();
}

} // namespace config
