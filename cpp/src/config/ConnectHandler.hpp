#pragma once

#include <string_view>

#include "DialOutcomes.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "web/ConnectionInfo.hpp"

namespace config {

// POST /config/api/connections, {"address": "http://127.0.0.1:8080"}: dials
// the address, and says what came of it.
class ConnectHandler {
public:
    static constexpr std::string_view path = "/config/api/connections";

    // outcomes must outlive this.
    explicit ConnectHandler(DialOutcomes& outcomes);

    // Throws what posting the dial asked for throws.
    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& connection) const;

private:
    DialOutcomes* m_outcomes;
};

} // namespace config
