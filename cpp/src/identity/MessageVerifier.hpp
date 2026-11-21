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

// Checks the signature on a request or an answer (HTTP API §11, §11.2): that
// it has exactly one, labelled libranet, covering @method and @path, or
// @status, and content-digest when there is a body; that its keyid is a node
// id; that it was made recently, by the node's clock, and has not expired
// (HandshakeProtocol §2); that the Content-Digest is the body's; and that
// the signer's key made it. A signer whose key is not held yet is told
// apart from a signature that fails, so that the key can be fetched while
// the bootstrap grace period lasts (HandshakeProtocol §3.2). Safe to use
// from any thread, so long as now is.
class MessageVerifier {
public:
    using Clock = std::chrono::system_clock;
    using Now = std::function<Clock::time_point()>;
    using Duration = std::chrono::milliseconds;

    // What identity.signature_max_age_seconds and
    // identity.signature_clock_skew_seconds are when not set, as the Python
    // node's are (HandshakeProtocol §2).
    static constexpr Duration default_max_age = std::chrono::seconds(5);
    static constexpr Duration default_clock_skew = std::chrono::seconds(30);

    // How far a signature's created time may be from the node's clock: no
    // more than max_age before it, nor ahead of it, each widened by
    // clock_skew, which an expires time is allowed too.
    struct Window {
        Duration max_age = default_max_age;
        Duration clock_skew = default_clock_skew;
    };

    // The node a signature names, by its keyid.
    struct Signer {
        storage::Identifier node_id;
        // Whether the node's key made the signature; false when the key is
        // not held yet, and the signature could not be checked with it.
        bool verified = false;
    };

    // window is identity.signature_max_age_seconds and
    // identity.signature_clock_skew_seconds. Throws std::invalid_argument
    // for no keys, a max_age that is not more than 0, a negative
    // clock_skew, or now with no function.
    MessageVerifier(std::shared_ptr<KnownKeys> keys, Window window, Now now = &Clock::now);

    // The node that signed the message: nothing when it has neither
    // Signature-Input nor Signature, and so is not signed. Each throws
    // std::invalid_argument for a signature that fails: one that is not as
    // HTTP API §11.2 says, is not fresh, does not cover what it must, has a
    // Content-Digest that is not the body's, or that the signer's key did
    // not make; and for a signer whose key, as the store holds it, is not
    // one (KnownKeys::key()).
    [[nodiscard]] std::optional<Signer> verify(const http::Request& request) const;
    [[nodiscard]] std::optional<Signer> verify(const http::Response& response) const;

private:
    std::shared_ptr<KnownKeys> m_keys;
    Window m_window;
    Now m_now;
};

} // namespace identity
