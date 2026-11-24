#pragma once

#include <chrono>
#include <functional>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "identity/NodeIdentity.hpp"

namespace identity {

// Signs what the node sends as the node (HTTP API §11): adds the
// Content-Digest of a message's body, when it has one, then a signature
// labelled libranet, in Signature-Input and Signature, covering what
// SignatureBase::of() covers. The signature says it was made at the time clock
// gives, and names the node's id as its keyid. Each replaces a field of its
// name the message has, as the Python node's MessageSigner does. Nothing
// changes once it is made, so any thread may use it, if clock may be called
// from any.
class MessageSigner {
public:
    using Clock = std::chrono::system_clock;
    using ClockFunction = std::function<Clock::time_point()>;

    // Signs with identity, which must outlive it. Throws
    // std::invalid_argument for clock with no function.
    explicit MessageSigner(const NodeIdentity& identity, ClockFunction clock = &Clock::now);

    // Each throws std::invalid_argument, as SignatureBase::of() does, for a
    // message it cannot sign: a request whose target has no path, or an
    // answer whose status is not three digits.
    void sign(http::Request& request) const;
    void sign(http::Response& response) const;

private:
    const NodeIdentity* m_identity = nullptr;
    ClockFunction m_clock;
};

} // namespace identity
