#pragma once

#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "Credential.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "web/ConnectionInfo.hpp"
#include "web/Server.hpp"
#include "web/SiteChecks.hpp"

namespace config {

// Stands in front of every handler on /config's port (HTTP API §2.3): turns
// away requests from elsewhere or from other sites, then checks the
// credential, or captures it when there is none.
class Guard {
public:
    using Handler = web::Server::RequestHandler;

    static constexpr std::string_view realm = "Libranet /config";

    static constexpr std::string_view policy_header = "Content-Security-Policy";

    // The reference node's Content-Security-Policy (HTTP API §2.3.3).
    static constexpr std::string_view policy =
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' "
        "'unsafe-inline'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'";

    // hosts are those /config is served as (network.config_hosts). Throws as
    // web::SiteChecks does.
    Guard(Credential credential, std::vector<std::string> hosts);

    // To be registered in handler's place; this need not outlive it. What
    // handler or the credential throws is thrown on.
    [[nodiscard]] Handler in_front_of(Handler handler) const;

private:
    Credential m_credential;
    web::SiteChecks m_checks;

    void _serve(const Handler& handler, const http::Request& request, http::Response& response,
                web::ConnectionInfo& connection) const;
    [[nodiscard]] std::optional<web::SiteChecks::Refusal>
    _refusal(const http::Request& request, const web::ConnectionInfo& connection) const;
    [[nodiscard]] bool _authenticates(const http::Request& request) const;
};

} // namespace config
