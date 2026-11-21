#pragma once

#include <cstddef>
#include <list>
#include <mutex>
#include <string>
#include <unordered_map>
#include <utility>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "identity/MessageSigner.hpp"
#include "identity/MessageVerifier.hpp"
#include "storage/Identifier.hpp"
#include "web/ConnectionInfo.hpp"
#include "web/Server.hpp"

namespace api {

// Stands in front of the node's handlers: checks the signature on each
// signed request before its handler sees it, and signs each answer as the
// node once it is made (HTTP API §11). A request whose signature verifies is
// served as from its signer, whom its connection then carries. One whose
// signer's key the node does not hold yet is served while the key is
// obtained, up to provisional_trust_attempts of them for each signer; the
// next is answered 401, and its connection closed (HandshakeProtocol §3.2).
// A signature that fails is answered 401, and its connection closed, at once
// (HandshakeProtocol §5.3). An unsigned PUT or POST is answered 401, as it
// names no node to attribute what it sends to, and any other unsigned
// request, a GET among them, is served (HandshakeProtocol §2.1). Safe to use
// from the threads serving connections, several at once.
class SignatureGuard {
public:
    using Handler = web::Server::RequestHandler;

    // What identity.provisional_trust_attempts is when not set, and the
    // least it may be, as the Python node's are (HTTP API §11.1).
    static constexpr std::size_t default_provisional_trust_attempts = 3;
    static constexpr std::size_t min_provisional_trust_attempts = 2;
    // How many signers whose keys are not held yet have their requests
    // counted, by default, as the Python node counts them. The one seen
    // least recently is forgotten first, so that requests naming endless
    // made-up node ids cannot grow what is counted without limit.
    static constexpr std::size_t default_tracked_signers = 4096;

    // Checks requests with verifier, and signs answers with signer. Throws
    // std::invalid_argument for provisional_trust_attempts less than
    // min_provisional_trust_attempts, or no tracked_signers.
    SignatureGuard(identity::MessageVerifier verifier, identity::MessageSigner signer,
                   std::size_t provisional_trust_attempts = default_provisional_trust_attempts,
                   std::size_t tracked_signers = default_tracked_signers);

    // handler, with this guard in front of it, to be registered with the
    // server in its place; this must outlive it. What handler throws, or
    // what the store throws as a key is read, is thrown on unsigned, for the
    // server to answer with 500.
    [[nodiscard]] Handler in_front_of(Handler handler);

    ~SignatureGuard() = default;
    SignatureGuard(const SignatureGuard&) = delete;
    SignatureGuard& operator=(const SignatureGuard&) = delete;
    SignatureGuard(SignatureGuard&&) = delete;
    SignatureGuard& operator=(SignatureGuard&&) = delete;

private:
    // How many requests each signer whose key is not held yet has sent, by
    // node id, least recently seen first.
    using Attempts = std::list<std::pair<std::string, std::size_t>>;

    identity::MessageVerifier m_verifier;
    identity::MessageSigner m_signer;
    std::size_t m_provisional_trust_attempts;
    std::size_t m_tracked_signers;
    std::mutex m_mutex;
    Attempts m_attempts;                                           // Guarded by m_mutex
    std::unordered_map<std::string, Attempts::iterator> m_signers; // Guarded by m_mutex

    void _serve(const Handler& handler, const http::Request& request, http::Response& response,
                web::ConnectionInfo& connection);
    [[nodiscard]] bool _admits(const http::Request& request, http::Response& response,
                               web::ConnectionInfo& connection);
    [[nodiscard]] std::size_t _attempt(const storage::Identifier& node_id);
    void _forget(const storage::Identifier& node_id);
    void _sign(const http::Request& request, http::Response& response) const;
};

} // namespace api
