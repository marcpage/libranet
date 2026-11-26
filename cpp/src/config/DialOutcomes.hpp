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

// What came of the dials /config asks peer::Connections for, handed to the
// requests waiting on them. It hears each address.dialed on a channel of its
// own, and hands each to the request waiting for its dial, by the id it gave
// that request to name its dial by. An outcome no request waits for is
// dropped. Any thread may use it.
class DialOutcomes {
public:
    using AddressDialed = peer::Connections::AddressDialed;
    using Milliseconds = std::chrono::milliseconds;
    // Asks for a dial, naming it by dial_id, as address.dial_requested does.
    using Ask = std::function<void(const std::string& dial_id)>;

    // How long an outcome may take to come once its dial is over, as the
    // Python node allows.
    static constexpr Milliseconds default_report_time{std::chrono::seconds(5)};

    // A request waits as long as one dial may take within limits: its
    // connect timeout, and two of its request timeouts (Handshake Protocol
    // §3), and report_time more.
    explicit DialOutcomes(const peer::PeerConnection::Limits& limits,
                          Milliseconds report_time = default_report_time);
    // Lets each request waiting go, and waits for each to leave.
    ~DialOutcomes();

    // How long a request waits for what came of its dial.
    [[nodiscard]] Milliseconds wait() const noexcept;

    // What came of the dial ask asks for, given the id to name it by. Nothing
    // when nothing came in time, or once stop() is called, when ask is not
    // called. Throws what ask throws.
    [[nodiscard]] std::optional<AddressDialed> await(const Ask& ask);

    // Lets each request waiting go at once, as the node stops, and each that
    // comes after.
    void stop();

    // Its channel's thread holds this object, so it is neither copied nor
    // moved.
    DialOutcomes(const DialOutcomes&) = delete;
    DialOutcomes& operator=(const DialOutcomes&) = delete;
    DialOutcomes(DialOutcomes&&) = delete;
    DialOutcomes& operator=(DialOutcomes&&) = delete;

private:
    Milliseconds m_wait;
    std::mutex m_mutex;
    // An outcome came, a request left, or it stopped.
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
