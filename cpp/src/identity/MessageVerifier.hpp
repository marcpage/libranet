#pragma once

#include <chrono>
#include <functional>
#include <memory>
#include <optional>

#include "KnownKeys.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "storage/Identifier.hpp"

namespace identity {

// Checks the signature on a request or an answer (HTTP API §11,
// HandshakeProtocol §2). Any thread may use it, if clock may be called from
// any.
class MessageVerifier {
public:
    using Clock = std::chrono::system_clock;
    using ClockFunction = std::function<Clock::time_point()>;
    using Milliseconds = std::chrono::milliseconds;

    // As the Python node's are (HandshakeProtocol §2).
    static constexpr Milliseconds default_max_age = std::chrono::seconds(5);
    static constexpr Milliseconds default_clock_skew = std::chrono::seconds(30);

    // A signature may be created no more than max_age before the node's clock,
    // nor ahead of it, each widened by clock_skew, as an expires time is.
    struct Window {
        Milliseconds max_age = default_max_age;
        Milliseconds clock_skew = default_clock_skew;
    };

    // By its keyid.
    struct Signer {
        storage::Identifier node_id;
        // False when the key is not held yet.
        bool verified = false;
    };

    // Throws std::invalid_argument for no keys, a max_age not more than 0, a
    // negative clock_skew, or no clock.
    MessageVerifier(std::shared_ptr<KnownKeys> keys, Window window,
                    ClockFunction clock = &Clock::now);

    // Nothing when the message is not signed. Each throws
    // std::invalid_argument for a signature that fails, or a signer whose key
    // the store holds is not one (KnownKeys::key()).
    [[nodiscard]] std::optional<Signer> verify(const http::Request& request) const;
    [[nodiscard]] std::optional<Signer> verify(const http::Response& response) const;

private:
    std::shared_ptr<KnownKeys> m_keys;
    Window m_window;
    ClockFunction m_clock;
};

} // namespace identity
