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
// §4.5), to the best outgoing connection that takes it (§4.6). It does not
// delete the object. Any number may run at once, from any threads.
class HandOff {
public:
    using Session = std::shared_ptr<PeerSession>;
    // Called from the threads handing off, several at once.
    using GetPeers = std::function<std::vector<Session>()>;

    // Throws std::invalid_argument for no store, or get_peers with no
    // function.
    HandOff(storage::IDataSource::Ptr store, GetPeers get_peers);

    // The node id of the peer that took it; nothing when none did, or the
    // store does not hold it. Throws what the store throws.
    [[nodiscard]] std::optional<storage::Identifier>
    hand_off(const storage::Identifier& identifier) const;

private:
    storage::IDataSource::Ptr m_store;
    GetPeers m_get_peers;
};

} // namespace peer
