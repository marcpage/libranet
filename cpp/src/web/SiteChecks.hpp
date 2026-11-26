#pragma once

#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "ConnectionInfo.hpp"
#include "http/Request.hpp"

namespace web {

// The checks that keep other sites' pages out of what only this machine is
// served (HTTP API §2.3.3, §2.4). A request carrying none of the headers
// they read comes from no browser, and passes.
class SiteChecks {
public:
    // The header a request is refused for, one of those below, and why.
    struct Refusal {
        std::string_view header;
        std::string reason;
    };

    static constexpr std::string_view host_header = "Host";
    static constexpr std::string_view site_header = "Sec-Fetch-Site";
    static constexpr std::string_view origin_header = "Origin";

    // hosts are shell-style patterns, as network.config_hosts gives them,
    // matched whatever their case, an IPv6 address without its brackets.
    // served is what a refusal calls what is checked, as "/config". Throws
    // std::invalid_argument for a host holding a NUL, and std::system_error
    // if the name this machine gives itself cannot be read.
    SiteChecks(std::vector<std::string> hosts, std::string served);

    // host_refusal(), or else page_refusal().
    [[nodiscard]] std::optional<Refusal> refusal(const http::Request& request,
                                                 const ConnectionInfo& connection) const;
    // Unless Host is not sent, names a host served as, or names the address
    // connection reached.
    [[nodiscard]] std::optional<Refusal> host_refusal(const http::Request& request,
                                                      const ConnectionInfo& connection) const;
    // By Sec-Fetch-Site where it is sent, and by Origin where it is not.
    [[nodiscard]] std::optional<Refusal> page_refusal(const http::Request& request) const;
    // Whether the host authority names is one of hosts, or this machine's
    // own name, whatever its port.
    [[nodiscard]] bool serves_as(std::string_view authority) const;

private:
    std::vector<std::string> m_hosts; // In lower case
    std::string m_machine_name;       // In lower case
    std::string m_served;
};

} // namespace web
