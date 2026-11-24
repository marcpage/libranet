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

// Stands in front of the node's handlers: checks the signature on each request
// before its handler sees it, and signs each answer as the node (HTTP API §11,
// HandshakeProtocol §2.1, §3.2, §5.3). Safe to use from the threads serving
// connections, several at once.
// cppcheck-suppress noConstructor
class SignatureGuard {
public:
    using Handler = web::Server::RequestHandler;

    // And the least it may be, as the Python node's (HTTP API §11.1).
    static constexpr std::size_t default_provisional_trust_attempts = 3;
    static constexpr std::size_t min_provisional_trust_attempts = 2;
    // Signers whose keys are not held yet whose requests are counted, as the
    // Python node counts them; the one seen least recently is forgotten first.
    static constexpr std::size_t default_tracked_signers = 4096;

    // Throws std::invalid_argument for provisional_trust_attempts less than
    // min_provisional_trust_attempts, or no tracked_signers.
    SignatureGuard(identity::MessageVerifier verifier, identity::MessageSigner signer,
                   std::size_t provisional_trust_attempts = default_provisional_trust_attempts,
                   std::size_t tracked_signers = default_tracked_signers);

    // To be registered with the server in handler's place; this must outlive
    // it. What handler or the store throws is thrown on.
    [[nodiscard]] Handler in_front_of(Handler handler);

    ~SignatureGuard() = default;

    SignatureGuard(const SignatureGuard&) = delete;
    SignatureGuard& operator=(const SignatureGuard&) = delete;
    SignatureGuard(SignatureGuard&&) = delete;
    SignatureGuard& operator=(SignatureGuard&&) = delete;

private:
    // By node id, least recently seen first.
    using Attempts = std::list<std::pair<std::string, std::size_t>>;

    identity::MessageVerifier m_verifier;
    identity::MessageSigner m_signer;
    // Mistaken: cppcheck loses the constructors in files `using` the class.
    // cppcheck-suppress-begin uninitMemberVarNoCtor
    std::size_t m_provisional_trust_attempts;
    std::size_t m_tracked_signers;
    // cppcheck-suppress-end uninitMemberVarNoCtor
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
