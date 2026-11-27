#pragma once

#include <string_view>

#include "Credential.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "web/ConnectionInfo.hpp"
#include "web/Server.hpp"

namespace config {

// Stands in front of every handler on /config's port (HTTP API §2.3). A
// request from a client elsewhere is refused, whatever it carries, and
// captures nothing. Then the /config credential is captured, when the node
// has none yet, or checked (§2.3.1). Only the connection's addresses and the
// request's Authorization field are looked at until then, and a request
// refused or challenged never reaches its handler, so nothing looks at its
// body. Safe to use from the threads serving connections, several at once.
class Guard {
public:
    using Handler = web::Server::RequestHandler;

    // Named in the challenge, so that a client can tell this credential from
    // another's.
    static constexpr std::string_view realm = "Libranet /config";

    explicit Guard(Credential credential);

    // To be registered with the server in handler's place. It keeps what it
    // needs of this, which need not outlive it. What handler or the
    // credential throws is thrown on.
    [[nodiscard]] Handler in_front_of(Handler handler) const;

private:
    Credential m_credential;

    void _serve(const Handler& handler, const http::Request& request, http::Response& response,
                web::ConnectionInfo& connection) const;
    [[nodiscard]] bool _authenticates(const http::Request& request) const;
};

} // namespace config
