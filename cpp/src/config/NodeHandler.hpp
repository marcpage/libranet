#pragma once

#include <string>
#include <string_view>
#include <vector>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "net/Address.hpp"
#include "storage/Identifier.hpp"
#include "web/ConnectionInfo.hpp"

namespace config {

// GET /config/api/node: the node's id, main port, and advertised endpoint
// (HTTP API §10.1).
class NodeHandler {
public:
    static constexpr std::string_view path = "/config/api/node";

    // own_endpoints are those the node list names the node at, best first.
    // Throws std::invalid_argument when there are none.
    NodeHandler(const storage::Identifier& own_id, const net::Address& main_address,
                const std::vector<std::string>& own_endpoints);

    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& /*connection*/) const;

private:
    std::string m_body;
};

} // namespace config
