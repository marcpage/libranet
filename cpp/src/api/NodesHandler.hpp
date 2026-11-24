#pragma once

#include <mutex>
#include <string>
#include <vector>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "protocol/NodeList.hpp"
#include "storage/Identifier.hpp"
#include "web/ConnectionInfo.hpp"

namespace api {

// Answers GET and POST of /data/nodes, the node lists nodes exchange
// (HttpApi §10.5), and any other method there with 405. A GET is the node
// list the node serves, as it is sent, within 1 MiB (§10.6): the node's own
// endpoints alone, until it is given the list stats::Recorder last derived.
// A POST is a peer's list, as it is sent, compressed or not, answered 202
// without waiting for its entries to be recorded, and posted as
// nodes.received, with each endpoint that names the sender's machine
// resolved to the address the connection came from (§10.2). The entries
// naming the node whose signature the connection carries are its own:
// advertised where it named the endpoint as it is, and observed where the
// endpoint was resolved. The rest it relayed. A body that is not a node
// list is 400, with the problem type invalid-list. A path beneath
// /data/nodes is 404: it names nothing.
class NodesHandler {
public:
    // Serves a list of the node's own endpoints, own_id at each of
    // own_endpoints, in the order given. Throws std::invalid_argument for an
    // endpoint that is not one, as protocol::NodeList does.
    NodesHandler(const storage::Identifier& own_id, const std::vector<std::string>& own_endpoints);
    ~NodesHandler() = default;

    NodesHandler() = delete;
    NodesHandler(const NodesHandler&) = delete;
    NodesHandler& operator=(const NodesHandler&) = delete;
    NodesHandler(NodesHandler&&) = delete;
    NodesHandler& operator=(NodesHandler&&) = delete;

    // Serves nodes from now on, as stats::Recorder last derived it. Called
    // from any thread, while requests are answered.
    void set_nodes(const protocol::NodeList& nodes);

    // Called from the threads serving connections, several at once. Throws
    // std::bad_alloc if zlib runs out of memory, which the server answers
    // with 500.
    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& connection) const;

private:
    mutable std::mutex m_mutex;
    std::string m_body; // Guarded by m_mutex: the list served, as it is sent

    void _get(http::Response& response) const;
    static void _post(const http::Request& request, http::Response& response,
                      const web::ConnectionInfo& connection);
};

} // namespace api
