#pragma once

#include <chrono>
#include <condition_variable>
#include <functional>
#include <map>
#include <mutex>
#include <optional>
#include <string>

#include "msg/AsyncChannel.hpp"
#include "msg/IMessage.hpp"
#include "peer/Connections.hpp"
#include "peer/PeerConnection.hpp"

namespace config {

// Hands what came of each dial /config asks peer::Connections for to the
// request waiting on it.
class DialOutcomes {
public:
    using AddressDialed = peer::Connections::AddressDialed;
    using Milliseconds = std::chrono::milliseconds;
    // Asks for a dial, naming it by dial_id, as address.dial_requested does.
    using Ask = std::function<void(const std::string& dial_id)>;

    // As the Python node allows.
    static constexpr Milliseconds default_report_time{std::chrono::seconds(5)};

    // A request waits as long as one dial may take within limits (Handshake
    // Protocol §3), and report_time more.
    explicit DialOutcomes(const peer::PeerConnection::Limits& limits,
                          Milliseconds report_time = default_report_time);
    // Lets each request waiting go, and waits for each to leave.
    ~DialOutcomes();

    // How long a request waits for what came of its dial.
    [[nodiscard]] Milliseconds wait_time() const noexcept;

    // What came of the dial ask asks for: nothing when nothing came in time,
    // or once stopped. Throws what ask throws.
    [[nodiscard]] std::optional<AddressDialed> await(const Ask& ask);

    // Lets each request waiting go, and each that comes after.
    void stop();

    // Its channel's thread holds this object, so it is neither copied nor
    // moved.
    DialOutcomes(const DialOutcomes&) = delete;
    DialOutcomes& operator=(const DialOutcomes&) = delete;
    DialOutcomes(DialOutcomes&&) = delete;
    DialOutcomes& operator=(DialOutcomes&&) = delete;

private:
    Milliseconds m_wait_time;
    std::mutex m_mutex;
    std::condition_variable m_changed;
    std::map<std::string, std::optional<AddressDialed>> m_awaited; // Guarded: by dial id
    bool m_stopped = false;                                        // Guarded by m_mutex

    // Made last, and so stopped first.
    msg::AsyncChannel m_channel;

    [[nodiscard]] std::optional<std::string> _expect();
    [[nodiscard]] std::optional<AddressDialed> _wait_for(const std::string& dial_id);
    void _forget(const std::string& dial_id);
    void _take(const msg::IMessage::Ptr& message);
};

} // namespace config
