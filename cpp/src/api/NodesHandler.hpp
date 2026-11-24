#pragma once

#include <cstddef>
#include <mutex>
#include <string>
#include <vector>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "protocol/NodeList.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"
#include "web/ConnectionInfo.hpp"

namespace api {

// Answers GET and POST of /data/nodes, the node lists nodes exchange (HttpApi
// §10.5). Until set_nodes(), the list served names the node's own endpoints
// alone.
class NodesHandler {
public:
    // list_bytes is the most a list posted may decompress to, and the list
    // served is sent in fewer than served_bytes. Throws
    // std::invalid_argument for an own endpoint that is not one.
    NodesHandler(const storage::Identifier& own_id, const std::vector<std::string>& own_endpoints,
                 std::size_t list_bytes = types::max_decompressed_list_size,
                 std::size_t served_bytes = protocol::NodeList::max_size);

    // Called from any thread, while requests are answered.
    void set_nodes(const protocol::NodeList& nodes);

    // Called from the threads serving connections, several at once. Throws
    // std::bad_alloc if zlib runs out of memory.
    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& connection) const;

    ~NodesHandler() = default;

    NodesHandler() = delete;
    NodesHandler(const NodesHandler&) = delete;
    NodesHandler& operator=(const NodesHandler&) = delete;
    NodesHandler(NodesHandler&&) = delete;
    NodesHandler& operator=(NodesHandler&&) = delete;

private:
    std::size_t m_list_bytes;
    std::size_t m_served_bytes;
    mutable std::mutex m_mutex;
    std::string m_body; // Guarded by m_mutex: the list served, as it is sent

    void _get(http::Response& response) const;
    void _post(const http::Request& request, http::Response& response,
               const web::ConnectionInfo& connection) const;
};

} // namespace api
