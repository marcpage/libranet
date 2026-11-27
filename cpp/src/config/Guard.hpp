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

// Stands in front of every handler on /config's port (HTTP API §2.3). A
// request from a client elsewhere is refused, whatever it carries, and
// captures nothing. So is one whose headers say another site's page made it,
// and one beneath /config/api that no page of /config made, as its Referer
// says (§2.3.3, §2.5). Then the /config credential is captured, when the node
// has none yet, or checked (§2.3.1). Only the connection's addresses and the
// request's headers are looked at until then, and a request refused or
// challenged never reaches its handler, so nothing looks at its body. Every
// answer carries policy, and no header granting a cross-origin request. Safe
// to use from the threads serving connections, several at once.
class Guard {
public:
    using Handler = web::Server::RequestHandler;

    // Named in the challenge, so that a client can tell this credential from
    // another's.
    static constexpr std::string_view realm = "Libranet /config";

    // The reference node's Content-Security-Policy (HTTP API §2.3.3): the
    // pages of /config load what they use only from its own origin, inline
    // scripts and styles aside, send their forms only to it, and are framed
    // by no page.
    static constexpr std::string_view policy =
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' "
        "'unsafe-inline'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'";

    // hosts are those /config is served as, as network.config_hosts gives
    // them (web::SiteChecks). Throws as web::SiteChecks does.
    Guard(Credential credential, std::vector<std::string> hosts);

    // To be registered with the server in handler's place. It keeps what it
    // needs of this, which need not outlive it. What handler or the
    // credential throws is thrown on.
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
