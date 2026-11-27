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

// Answers GET of /config/api/node: what the node is, as the Python node says
// it. That is the node's id, the address and port its main port listens at,
// and the endpoint it advertises, the first its node list names it at (HTTP
// API §10.1). Any other method is 405, and any path beneath it 404. Safe to
// use from the threads serving connections, several at once.
class NodeHandler {
public:
    // Where it is registered.
    static constexpr std::string_view path = "/config/api/node";

    // main_address is where the main port listens, and own_endpoints are
    // those the node list names the node at, best first, as api::Service
    // gives them. Throws std::invalid_argument when there are none.
    NodeHandler(const storage::Identifier& own_id, const net::Address& main_address,
                const std::vector<std::string>& own_endpoints);

    // Called from the threads serving connections, several at once.
    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& connection) const;

private:
    std::string m_body; // What a GET is answered, as it is sent
};

} // namespace config
