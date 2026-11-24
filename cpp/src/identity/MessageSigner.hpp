#pragma once

#include <chrono>
#include <functional>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "identity/NodeIdentity.hpp"

namespace identity {

// Signs what the node sends as the node (HTTP API §11), as the Python node's
// MessageSigner does. Any thread may use it, if clock may be called from any.
class MessageSigner {
public:
    using Clock = std::chrono::system_clock;
    using ClockFunction = std::function<Clock::time_point()>;

    // identity must outlive it. Throws std::invalid_argument for no clock.
    explicit MessageSigner(const NodeIdentity& identity, ClockFunction clock = &Clock::now);

    // Each throws std::invalid_argument for a message SignatureBase::of()
    // cannot sign.
    void sign(http::Request& request) const;
    void sign(http::Response& response) const;

private:
    const NodeIdentity* m_identity = nullptr;
    ClockFunction m_clock;
};

} // namespace identity
