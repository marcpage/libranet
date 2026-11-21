#include "SignatureGuard.hpp"

#include <cstddef>
#include <iterator>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>

#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "identity/MessageSigner.hpp"
#include "identity/MessageVerifier.hpp"
#include "storage/Identifier.hpp"
#include "web/ConnectionInfo.hpp"

using api::SignatureGuard;
using http::Problem;
using http::Request;
using http::Response;
using identity::MessageSigner;
using identity::MessageVerifier;
using std::invalid_argument;
using std::mutex;
using std::optional;
using std::scoped_lock;
using std::size_t;
using std::string;
using std::to_string;
using storage::Identifier;
using web::ConnectionInfo;

namespace {

using Status = http::ResponseCode::Status;

// Whether request sends what must be attributed to a node, and so must be
// signed: an upload, or a published list (HandshakeProtocol §2.1).
bool needs_signature(const Request& request) {
    return "PUT" == request.method() || "POST" == request.method();
}

// Answers 401 for an unsigned request that must be signed.
void answer_signature_required(Response& response) {
    Problem(Problem::Type::SignatureRequired, Status::Unauthorized,
            "This request must carry the sending node's signature.")
        .answer(response);
}

// Answers 401 for a signature refused, saying why, and has the connection
// closed once it is sent (HandshakeProtocol §5.3).
void refuse(const string& reason, Response& response, ConnectionInfo& connection) {
    Problem(Problem::Type::InvalidSignature, Status::Unauthorized, reason).answer(response);
    connection.close_after_answer();
}

size_t checked_attempts(size_t provisional_trust_attempts) {
    if (provisional_trust_attempts < SignatureGuard::min_provisional_trust_attempts) {
        throw invalid_argument("identity.provisional_trust_attempts must be at least "
                               + to_string(SignatureGuard::min_provisional_trust_attempts));
    }

    return provisional_trust_attempts;
}

size_t checked_signers(size_t tracked_signers) {
    if (0 == tracked_signers) {
        throw invalid_argument("A SignatureGuard must count the requests of at least one signer");
    }

    return tracked_signers;
}

} // namespace

namespace api {

SignatureGuard::SignatureGuard(MessageVerifier verifier, MessageSigner signer,
                               size_t provisional_trust_attempts, size_t tracked_signers)
    : m_verifier(std::move(verifier))
    , m_signer(std::move(signer))
    , m_provisional_trust_attempts(checked_attempts(provisional_trust_attempts))
    , m_tracked_signers(checked_signers(tracked_signers)) {}

SignatureGuard::Handler SignatureGuard::in_front_of(Handler handler) {
    return [this, handler = std::move(handler)](const Request& request, Response& response,
                                                ConnectionInfo& connection) {
        _serve(handler, request, response, connection);
    };
}

// Has handler answer request, if it is admitted, and signs the answer.
void SignatureGuard::_serve(const Handler& handler, const Request& request, Response& response,
                            ConnectionInfo& connection) {
    if (_admits(request, response, connection)) {
        handler(request, response, connection);
    }

    _sign(request, response);
}

// Whether request is to be served; when not, it has been answered.
// A signer whose key verified it is carried by the connection from now on,
// and its earlier requests, sent before its key was held, are forgotten.
bool SignatureGuard::_admits(const Request& request, Response& response,
                             ConnectionInfo& connection) {
    optional<MessageVerifier::Signer> signer;

    try {
        signer = m_verifier.verify(request);
    } catch (const invalid_argument& error) {
        refuse(error.what(), response, connection);
        return false;
    }

    if (!signer) {
        if (needs_signature(request)) {
            answer_signature_required(response);
            return false;
        }

        return true;
    }

    if (signer->verified) {
        _forget(signer->node_id);
        connection.set_node(signer->node_id);
        return true;
    }

    if (_attempt(signer->node_id) > m_provisional_trust_attempts) {
        refuse("The public key of " + signer->node_id.to_string() + " was not obtained within "
                   + to_string(m_provisional_trust_attempts) + " requests",
               response, connection);
        return false;
    }

    return true;
}

// Counts a request from node_id, whose key is not held yet, and says how
// many it has sent, this one included.
size_t SignatureGuard::_attempt(const Identifier& node_id) {
    const scoped_lock<mutex> lock(m_mutex);
    const auto key = node_id.to_string();
    auto found = m_signers.find(key);

    if (found == m_signers.end()) {
        m_attempts.emplace_back(key, 0);
        found = m_signers.emplace(key, std::prev(m_attempts.end())).first;
    } else {
        m_attempts.splice(m_attempts.end(), m_attempts, found->second);
    }

    const auto attempts = ++found->second->second;

    // The signer just counted is the last of at least one kept.
    while (m_attempts.size() > m_tracked_signers) {
        m_signers.erase(m_attempts.front().first);
        m_attempts.pop_front();
    }

    return attempts;
}

// Stops counting the requests of node_id, whose key is now held.
void SignatureGuard::_forget(const Identifier& node_id) {
    const scoped_lock<mutex> lock(m_mutex);
    const auto found = m_signers.find(node_id.to_string());

    if (found != m_signers.end()) {
        m_attempts.erase(found->second);
        m_signers.erase(found);
    }
}

// Signs response as it is sent: an answer to a HEAD goes without its body,
// so its signature covers none.
void SignatureGuard::_sign(const Request& request, Response& response) const {
    if ("HEAD" != request.method()) {
        m_signer.sign(response);
        return;
    }

    auto body = string(response.body());

    response.set_body("");
    m_signer.sign(response);
    response.set_body(std::move(body));
}

} // namespace api
