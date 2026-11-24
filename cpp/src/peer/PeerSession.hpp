#pragma once

#include <memory>
#include <stdexcept>
#include <string_view>
#include <vector>

#include "PeerConnection.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "identity/MessageSigner.hpp"
#include "identity/MessageVerifier.hpp"
#include "net/Address.hpp"
#include "storage/Identifier.hpp"

namespace peer {

// A connection to one node, by its id, once it is known which node it is
// (Handshake Protocol §3): each request is signed as this node, and each
// answer must be signed by that node, with its key (HTTP API §11). An answer
// that is not, as one unsigned, signed by another node, or whose signature
// fails, ends the session: the connection closes, and the requests still
// waiting fail. Any thread may use it, as it may the signer and the verifier.
class PeerSession {
public:
    // Why an answer was not given: it could not be shown to come from the
    // node, and so the session ended.
    class Unauthenticated : public std::runtime_error {
    public:
        using std::runtime_error::runtime_error;
    };

    // Takes over connection, to the node whose id is node_id. Requests are
    // signed with signer, and answers checked with verifier, whose keys must
    // hold the node's. Throws std::invalid_argument for no connection, or a
    // node_id with no hash.
    PeerSession(std::unique_ptr<PeerConnection> connection, storage::Identifier node_id,
                identity::MessageSigner signer, identity::MessageVerifier verifier);

    // Signs each request, sends them one after another without waiting for
    // each answer, and gives their answers, in the same order, each once it
    // is shown to come from the node. Throws Unauthenticated for an answer
    // that is not, once the session has ended; what an answer fails with, as
    // PeerConnection::send() says; PeerConnection::Closed once the session
    // has ended; and std::invalid_argument for a request that cannot be
    // signed, before any is sent, or that cannot be sent, after those before
    // it are.
    [[nodiscard]] std::vector<http::Response> exchange(std::vector<http::Request> requests);

    // The node every answer must be signed by.
    [[nodiscard]] const storage::Identifier& node_id() const noexcept;
    [[nodiscard]] const net::Address& address() const noexcept;
    // Whether requests are still taken.
    [[nodiscard]] bool is_open() const;
    // Whether the node turned this node away, answering with
    // Connection: close.
    [[nodiscard]] bool was_turned_away() const;
    [[nodiscard]] std::size_t matching_bits(const storage::Identifier& identifier) const;

    // Ends the session, failing every request still waiting.
    void close();

private:
    std::unique_ptr<PeerConnection> m_connection;
    storage::Identifier m_node_id;
    identity::MessageSigner m_signer;
    identity::MessageVerifier m_verifier;

    void _check(const http::Response& answer, std::string_view asked);
};

} // namespace peer
