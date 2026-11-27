#pragma once

#include <string_view>

#include "DialOutcomes.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "web/ConnectionInfo.hpp"

namespace config {

// POST /config/api/connections: has peer::Connections dial the address a
// request names, {"address": "http://127.0.0.1:8080"}, at once, and says what
// came of it once it hears. The body is read as JSON only when its
// Content-Type says it is, since a page on another site can send a form's
// types without asking this node first (HTTP API §2.3.3). Safe to use from
// the threads serving connections, several at once.
class ConnectHandler {
public:
    static constexpr std::string_view path = "/config/api/connections";

    // What came of each dial is waited for in outcomes, which must outlive
    // this.
    explicit ConnectHandler(DialOutcomes& outcomes);

    // Throws what posting the dial asked for throws.
    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& connection) const;

private:
    DialOutcomes* m_outcomes;
};

} // namespace config
