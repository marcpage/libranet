#pragma once

#include <cstddef>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "PeerSession.hpp"
#include "protocol/NodeList.hpp"
#include "storage/Identifier.hpp"

namespace peer {

// Tells the nodes this node is connected to of the entries its node list newly
// names (Handshake Protocol §3.3). Any thread may use it.
class NodeListPublisher {
public:
    using Sessions = std::vector<std::shared_ptr<PeerSession>>;

    // own_endpoints are named first in every list posted. Throws
    // std::invalid_argument for an own_id with no hash, or an own endpoint
    // that is not one.
    NodeListPublisher(const storage::Identifier& own_id,
                      const std::vector<std::string>& own_endpoints);

    // Nothing when added names no entry but node_id's or this node's.
    [[nodiscard]] std::optional<protocol::NodeList>
    list_for(const protocol::NodeList& added, const storage::Identifier& node_id) const;

    // Posts each open session its list, each on a thread of its own, and waits
    // for every answer. A failure is logged. Gives how many nodes accepted
    // theirs.
    [[nodiscard]] std::size_t publish(const protocol::NodeList& added,
                                      const Sessions& sessions) const;

private:
    storage::Identifier m_own_id;
    protocol::NodeList m_own_entries;
};

} // namespace peer
