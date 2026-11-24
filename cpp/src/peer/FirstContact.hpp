#pragma once

#include <memory>

#include "PeerConnection.hpp"
#include "PeerSession.hpp"
#include "SeekFulfiller.hpp"
#include "identity/MessageSigner.hpp"
#include "identity/MessageVerifier.hpp"
#include "identity/NodeIdentity.hpp"
#include "protocol/NodeList.hpp"
#include "protocol/SeekList.hpp"
#include "storage/IDataSource.hpp"

namespace peer {

// The first-contact exchange, as the client (Handshake Protocol §3). Nothing
// changes once it is made, so any thread may use it, each with a connection of
// its own.
class FirstContact {
public:
    // seek is nothing when the node sent no seek list that could be read.
    struct Contact {
        PeerSession session;
        protocol::SeekList seek;
    };

    // identity must outlive it. A peer's key that is not held is put in store,
    // which must be the one verifier reads keys from. Throws
    // std::invalid_argument for no store or no fulfiller.
    FirstContact(const identity::NodeIdentity& identity, storage::IDataSource::Ptr store,
                 identity::MessageVerifier verifier, std::shared_ptr<SeekFulfiller> fulfiller);

    // Gives the session with the node that proved its identity over
    // connection, whichever node it is. Throws PeerSession::Unauthenticated
    // when the peer did not prove which node it is; what
    // PeerSession::exchange() and PeerConnection::send() throw; and
    // std::invalid_argument for no connection.
    [[nodiscard]] Contact make(std::unique_ptr<PeerConnection> connection,
                               const protocol::NodeList& own_list,
                               const protocol::SeekList& own_seek) const;

private:
    const identity::NodeIdentity* m_identity = nullptr;
    storage::IDataSource::Ptr m_store;
    identity::MessageSigner m_signer;
    identity::MessageVerifier m_verifier;
    std::shared_ptr<SeekFulfiller> m_fulfiller;
};

} // namespace peer
