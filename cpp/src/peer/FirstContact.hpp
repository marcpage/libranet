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
#include "storage/Identifier.hpp"

namespace peer {

// The first-contact exchange, as the client (Handshake Protocol §3), adding
// value before asking for any: it puts this node's public key, so that the
// peer can check what follows; gets the peer's key, which the signature on
// the first answer names, unless it is held; posts this node's node list;
// reads the peer's seek list and node list; fulfils the peer's seek list,
// with a SeekFulfiller; and asks the peer for what this node seeks. The
// nodes the peer's list names are posted as nodes.received, for
// stats::Recorder to keep. Nothing changes once it is made, so any thread
// may use it, each with a connection of its own.
class FirstContact {
public:
    // A node first contact was made with: the session with it, and what it
    // seeks, which is nothing when it sent no seek list that could be read.
    struct Contact {
        PeerSession session;
        protocol::SeekList seek;
    };

    // Signs as identity, which must outlive it, and checks answers with
    // verifier. A peer's key that is not held is put in store, which must be
    // the store verifier's keys are read from, as is what this node seeks
    // that the peer sends. The peer's seek list is fulfilled with fulfiller.
    // Throws std::invalid_argument for no store or no fulfiller.
    FirstContact(const identity::NodeIdentity& identity, storage::IDataSource::Ptr store,
                 identity::MessageVerifier verifier, std::shared_ptr<SeekFulfiller> fulfiller);

    // Makes first contact over connection, posting own_list, this node's node
    // list, and gives the session with the node that proved its identity
    // there, whichever node it is. The peer's node list is posted as
    // nodes.received, unless it names no node but this one: each endpoint
    // naming the peer's machine resolved to the host connection dialed
    // (HttpApi §10.2), the entries naming this node left out, and the peer's
    // own entries said to be observed where they were resolved, and
    // advertised where they were not. A peer's key fetched is posted as
    // data.stored, from the peer. Then, unless the peer has turned this node
    // away, its seek list is fulfilled, and it is asked for each of
    // own_seek's data entries, what this node seeks, that the store does not
    // hold, in runs of the fulfiller's pipeline depth; each it sends that is
    // the content is put in the store and posted as data.stored, from the
    // peer. Throws PeerSession::Unauthenticated when the peer did not prove
    // which node it is: its first answer is unsigned, or its signature
    // fails, or it did not send the key that signed it, or it signed the
    // answer that brought the key with another; a key that came is not kept
    // then. Throws what PeerSession::exchange() throws, and what
    // PeerConnection::send() and its answers throw for the first two steps;
    // and std::invalid_argument for no connection.
    [[nodiscard]] Contact make(std::unique_ptr<PeerConnection> connection,
                               const protocol::NodeList& own_list,
                               const protocol::SeekList& own_seek) const;

private:
    const identity::NodeIdentity* m_identity = nullptr;
    storage::IDataSource::Ptr m_store;
    identity::MessageSigner m_signer;
    identity::MessageVerifier m_verifier;
    std::shared_ptr<SeekFulfiller> m_fulfiller;

    [[nodiscard]] storage::Identifier _identify(PeerConnection& connection) const;
    void _hold_key(PeerConnection& connection, const storage::Identifier& node_id) const;
};

} // namespace peer
