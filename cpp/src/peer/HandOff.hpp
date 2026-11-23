#pragma once

#include <functional>
#include <memory>
#include <optional>
#include <vector>

#include "PeerSession.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"

namespace peer {

// The hand-off of an object the node means to let go of (High-Level Design
// §4.5). It is offered to the best outgoing connection (§4.6), the peer
// whose node id shares the most leading bits with its hash, and, while one
// does not take it, to the next best, until one does or none is left. Each
// peer connected as it begins is offered it once, and those that match
// alike in the order given. It is put as it is stored, compressed or not, as
// a peer checks either (HttpApi §7.1). A peer takes it when it answers with
// success, as one that holds it already does. One that answers otherwise,
// or that cannot be asked, as its connection closed or failed or its answer
// could not be shown to come from it, does not.
//
// The object is not deleted here: the one that asked deletes it, once a
// peer took it. Each hand-off runs on the thread that asks for it, and any
// number may run at once, from any threads.
class HandOff {
public:
    using Session = std::shared_ptr<PeerSession>;
    // The sessions with the peers this node has an outgoing connection to,
    // as they are now. Called from the threads handing off, several at once.
    using Peers = std::function<std::vector<Session>()>;

    // Offers what store holds to the peers peers gives. Throws
    // std::invalid_argument for no store, or peers with no function.
    HandOff(storage::IDataSource::Ptr store, Peers peers);

    // Offers identifier's content to the peers, best match first, until one
    // takes it, and gives the node id of the one that did; nothing when none
    // did, or the store does not hold it. Throws what the store throws.
    [[nodiscard]] std::optional<storage::Identifier>
    hand_off(const storage::Identifier& identifier) const;

private:
    storage::IDataSource::Ptr m_store;
    Peers m_peers;
};

} // namespace peer
