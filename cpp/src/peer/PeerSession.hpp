#pragma once

#include <cstddef>
#include <memory>
#include <stdexcept>
#include <vector>

#include "PeerConnection.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "identity/MessageSigner.hpp"
#include "identity/MessageVerifier.hpp"
#include "net/Address.hpp"
#include "storage/Identifier.hpp"

namespace peer {

// A connection to one node, once it is known which node it is (Handshake
// Protocol §3): each request is signed, and an answer the node did not sign
// ends the session (HTTP API §11). Any thread may use it.
class PeerSession {
public:
    // The answer could not be shown to come from the node, so the session
    // ended.
    class Unauthenticated : public std::runtime_error {
    public:
        using std::runtime_error::runtime_error;
    };

    // verifier's keys must hold the node's. Throws std::invalid_argument for
    // no connection, or a node_id with no hash.
    PeerSession(std::unique_ptr<PeerConnection> connection, storage::Identifier node_id,
                identity::MessageSigner signer, identity::MessageVerifier verifier);

    // Sends the requests without waiting for each answer, and gives the
    // answers in the same order. Throws Unauthenticated for an answer the node
    // did not sign; what PeerConnection::send() says; and
    // std::invalid_argument for a request that cannot be signed or sent.
    [[nodiscard]] std::vector<http::Response> exchange(std::vector<http::Request> requests);

    // The node every answer must be signed by.
    [[nodiscard]] const storage::Identifier& node_id() const noexcept;
    [[nodiscard]] const net::Address& address() const noexcept;
    [[nodiscard]] bool is_open() const;
    // Answering with Connection: close.
    [[nodiscard]] bool was_turned_away() const;
    [[nodiscard]] std::size_t matching_bits(const storage::Identifier& identifier) const;

    // The sessions of sessions still open, those whose node ids best match
    // identifier first, and those that match alike in the order given.
    [[nodiscard]] static std::vector<std::shared_ptr<PeerSession>>
    ranked(std::vector<std::shared_ptr<PeerSession>> sessions,
           const storage::Identifier& identifier);

    // Fails every request still waiting.
    void close();

private:
    std::unique_ptr<PeerConnection> m_connection;
    storage::Identifier m_node_id;
    identity::MessageSigner m_signer;
    identity::MessageVerifier m_verifier;
};

} // namespace peer
