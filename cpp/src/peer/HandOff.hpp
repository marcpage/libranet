#pragma once

#include <functional>
#include <memory>
#include <optional>
#include <vector>

#include "PeerSession.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"

namespace peer {

// Hands an object the node means to let go of to the best outgoing connection
// that takes it (High-Level Design §4.5, §4.6), without deleting it. Any
// thread may use it.
class HandOff {
public:
    using Session = std::shared_ptr<PeerSession>;
    // Called from the threads handing off, several at once.
    using Peers = std::function<std::vector<Session>()>;

    // Throws std::invalid_argument for no store, or peers with no
    // function.
    HandOff(storage::IDataSource::Ptr store, Peers peers);

    // The node id of the peer that took it; nothing when none did, or the
    // store does not hold it. Throws what the store throws.
    [[nodiscard]] std::optional<storage::Identifier>
    hand_off(const storage::Identifier& identifier) const;

private:
    storage::IDataSource::Ptr m_store;
    Peers m_peers;
};

} // namespace peer
