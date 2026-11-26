#include "SiteChecks.hpp"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstddef>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#include <fnmatch.h>
#include <unistd.h>

#include "ConnectionInfo.hpp"
#include "http/Headers.hpp"
#include "http/Request.hpp"
#include "http/Url.hpp"
#include "net/IpAddress.hpp"
#include "types/Converter.hpp"

using http::Headers;
using http::Request;
using http::Url;
using net::IpAddress;
using std::any_of;
using std::array;
using std::generic_category;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::size_t;
using std::string;
using std::string_view;
using std::system_error;
using std::vector;
using types::Converter;
using web::ConnectionInfo;
using web::SiteChecks;

namespace {

// As long as a host name may be on macOS (MAXHOSTNAMELEN). Linux allows 64.
constexpr size_t machine_name_size = 256;

// What Sec-Fetch-Site says of a request a page of the same origin made, and
// of one the person using the browser asked for, by typing an address or
// opening a bookmark (W3C Fetch Metadata Request Headers).
constexpr string_view same_origin = "same-origin";
constexpr string_view user_initiated = "none";

// The name this machine gives itself, in lower case.
string machine_name() {
    // One more than is asked for, so that a name cut short still ends in NUL.
    array<char, machine_name_size + 1> name{};

    if (0 != ::gethostname(name.data(), machine_name_size)) {
        throw system_error(errno, generic_category(), "gethostname");
    }

    return Converter::lower_case(name.data());
}

// fnmatch() reads a pattern only up to a NUL, so one holding a NUL would be
// matched as if it ended there.
vector<string> checked_hosts(vector<string> hosts) {
    for (auto& host : hosts) {
        if (string::npos != host.find('\0')) {
            throw invalid_argument("A host to serve as cannot hold a NUL");
        }

        host = Converter::lower_case(host);
    }

    return hosts;
}

// The host and port authority, as a Host header gives it, names (RFC 9110
// §7.2), as a URL of no scheme. Nothing when it names no host, or more than a
// host and port. Url gives a URL with no path the path "/".
optional<Url> host_url(string_view authority) {
    auto url = Url::parse(Headers::trim_whitespace(authority));

    if (!url || url->host().empty() || !url->scheme().empty() || !url->username().empty()
        || !url->password().empty() || "/" != url->path() || !url->query().empty()
        || !url->fragment().empty()) {
        return nullopt;
    }

    return url;
}

// As Python's fnmatch does, a backslash in pattern matches itself rather than
// escaping what follows it.
bool matches(const string& pattern, const string& name) {
    return 0 == ::fnmatch(pattern.c_str(), name.c_str(), FNM_NOESCAPE);
}

// Whether authority names the IP address address, whatever its port. An
// IPv4-mapped address names the IPv4 address it maps.
bool names_address(string_view authority, string_view address) {
    const auto url = host_url(authority);
    const auto named = url ? IpAddress::parse(url->host()) : nullopt;

    return named.has_value() && named == IpAddress::parse(address);
}

// Whether origin, as an Origin header gives it, names the host and port host
// does. "null" names neither, so it never does.
bool same_authority(string_view origin, string_view host) {
    const auto origin_url = Url::parse(Headers::trim_whitespace(origin));
    auto host_named = host_url(host);

    if (!origin_url || !host_named || origin_url->scheme().empty()) {
        return false;
    }

    // An origin is a scheme, a host, and a port (RFC 6454 §7), and a Host
    // header names the last two.
    host_named->set_scheme(string(origin_url->scheme()));
    return *origin_url == *host_named;
}

string quoted(string_view text) {
    return "'" + string(text) + "'";
}

// How a refusal of a request another site's page made starts.
string own_pages_only(const string& served) {
    return served + " is served only to this node's own pages; ";
}

optional<SiteChecks::Refusal> site_refusal(const string& served, string_view site) {
    const auto said = Converter::lower_case(Headers::trim_whitespace(site));

    if (same_origin == said || user_initiated == said) {
        return nullopt;
    }

    auto reason = own_pages_only(served) + "Sec-Fetch-Site is " + quoted(site) + ".";

    return SiteChecks::Refusal{SiteChecks::site_header, std::move(reason)};
}

optional<SiteChecks::Refusal> origin_refusal(const string& served, const Headers& headers) {
    const auto origin = headers.get(SiteChecks::origin_header);
    const auto host = headers.get(SiteChecks::host_header);

    if (!origin || (host && same_authority(*origin, *host))) {
        return nullopt;
    }

    auto reason = own_pages_only(served) + "Origin is " + quoted(*origin) + " and Host is "
                  + quoted(host.value_or("")) + ".";

    return SiteChecks::Refusal{SiteChecks::origin_header, std::move(reason)};
}

} // namespace

namespace web {

SiteChecks::SiteChecks(vector<string> hosts, string served)
    : m_hosts(checked_hosts(std::move(hosts)))
    , m_machine_name(machine_name())
    , m_served(std::move(served)) {}

optional<SiteChecks::Refusal> SiteChecks::refusal(const Request& request,
                                                  const ConnectionInfo& connection) const {
    if (auto refused = host_refusal(request, connection)) {
        return refused;
    }

    return page_refusal(request);
}

// A site can point a name of its own at this machine, and a browser then
// takes its pages' requests to be same-origin, so that only this check
// catches them. It cannot point an address, so the one reached passes.
optional<SiteChecks::Refusal> SiteChecks::host_refusal(const Request& request,
                                                       const ConnectionInfo& connection) const {
    const auto host = request.headers().get(host_header);

    if (!host || serves_as(*host) || names_address(*host, connection.local_address().host())) {
        return nullopt;
    }

    auto reason = m_served + " is not served as " + quoted(*host)
                  + ", which the Host header names (it is served as the address it is reached "
                    "at, this machine's own name, and as network.config_hosts lists).";

    return Refusal{host_header, std::move(reason)};
}

optional<SiteChecks::Refusal> SiteChecks::page_refusal(const Request& request) const {
    const auto& headers = request.headers();

    if (const auto site = headers.get(site_header)) {
        return site_refusal(m_served, *site);
    }

    return origin_refusal(m_served, headers);
}

bool SiteChecks::serves_as(string_view authority) const {
    const auto url = host_url(authority);

    if (!url) {
        return false;
    }

    const auto name = Converter::lower_case(url->host());

    // fnmatch() would read name only up to a NUL, and let what follows it
    // through unread.
    if (string::npos != name.find('\0')) {
        return false;
    }

    const auto names = [&name](const string& pattern) { return matches(pattern, name); };

    return m_machine_name == name || any_of(m_hosts.begin(), m_hosts.end(), names);
}

} // namespace web
