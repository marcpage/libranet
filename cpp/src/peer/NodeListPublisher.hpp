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

// Tells the nodes this node is connected to of the entries its node list
// newly names, as once it has reached a node at an address it did not name
// before, so that they learn of nodes this node reaches after their first
// contact (Handshake Protocol §3.3). Each is posted those entries, with this
// node's own first (HttpApi §10.1), as a node list (§10.5), leaving out an
// entry naming that node, and is posted nothing when no other is left, as
// when this node has just reached it. Any thread may use it.
class NodeListPublisher {
public:
    using Sessions = std::vector<std::shared_ptr<PeerSession>>;

    // own_id and own_endpoints are this node's, which every list posted names
    // first, in the order given. Throws std::invalid_argument for an own_id
    // with no hash, or an own endpoint that is not one.
    NodeListPublisher(const storage::Identifier& own_id,
                      const std::vector<std::string>& own_endpoints);

    // The list to post to the node node_id of added: this node's own entries,
    // then those of added that name neither node_id nor this node, in the
    // order added names them. Nothing when added names no such entry.
    [[nodiscard]] std::optional<protocol::NodeList>
    list_for(const protocol::NodeList& added, const storage::Identifier& node_id) const;

    // Posts to each of sessions that is open the list list_for() gives its
    // node, each on a thread of its own, so that a node slow to answer holds
    // up none of the others, and waits for every answer. A post that fails,
    // or that is not answered 202 Accepted, is logged. Gives how many nodes
    // accepted their list.
    [[nodiscard]] std::size_t publish(const protocol::NodeList& added,
                                      const Sessions& sessions) const;

private:
    storage::Identifier m_own_id;
    protocol::NodeList m_own_entries;
};

} // namespace peer
