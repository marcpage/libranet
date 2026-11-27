#include "DialRequest.hpp"

#include <algorithm>
#include <cctype>
#include <charconv>
#include <cstddef>
#include <cstdint>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>

#include "http/Url.hpp"
#include "net/IpAddress.hpp"
#include "types/Converter.hpp"
#include "types/Json.hpp"

using http::Url;
using net::IpAddress;
using std::all_of;
using std::errc;
using std::from_chars;
using std::invalid_argument;
using std::isalnum;
using std::next;
using std::ptrdiff_t;
using std::string;
using std::string_view;
using std::uint16_t;
using types::Converter;
using types::Json;

namespace {

// The only scheme the node dials (HttpApi §3.1), and its port, for an
// address that names none.
constexpr string_view http_scheme = "http://";
constexpr uint16_t http_port = 80;

// Why address is refused when it names its host by neither a name nor an IP
// address.
invalid_argument not_a_host(string_view address) {
    return invalid_argument("An address must name its host by a name or an IP address, "
                            "an IPv6 one in brackets: "
                            + string(address));
}

// The authority of address, between its "http://" and its path, query, or
// fragment, less any user info, which the node does not dial with. Throws
// invalid_argument for an address that is not an http URL.
string_view authority_of(string_view address) {
    const auto scheme = Converter::lower_case(address.substr(0, http_scheme.size()));

    if (http_scheme != scheme) {
        throw invalid_argument("An address must start with http://, as "
                               "http://192.168.1.10:8080 does: "
                               + string(address));
    }

    auto authority = address.substr(http_scheme.size());

    authority = authority.substr(0, authority.find_first_of("/?#"));

    const auto user_info_end = authority.rfind('@');

    if (user_info_end != string_view::npos) {
        authority.remove_prefix(user_info_end + 1);
    }

    return authority;
}

// The host and port authority names, as a URL of no scheme, as Url reads
// them: no host when it is empty. Throws invalid_argument, naming address, for
// one naming a host with a bracket in it other than those around an IPv6
// address, from which Url reads no URL.
Url host_url_of(string_view authority, string_view address) {
    const auto url = Url::parse(authority);

    if (!url && !authority.empty()) {
        throw not_a_host(address);
    }

    return url.value_or(Url());
}

// Whether host is a name or an IPv4 address, of the characters RFC 3986
// §2.3 leaves unreserved, as a node list's endpoint names one.
bool is_host_name(string_view host) {
    return !host.empty() && all_of(host.begin(), host.end(), [](char character) {
        return isalnum(static_cast<unsigned char>(character)) != 0
               || Url::unreserved.find(character) != string_view::npos;
    });
}

// Whether host, as Url reads it, out of its brackets, is an IPv6 address.
bool is_ipv6(string_view host) {
    return host.find(':') != string_view::npos && IpAddress::parse(host).has_value();
}

// host, as Url reads it from an address, as it is dialed: in lower case.
// Throws invalid_argument, naming address, for a host that is not one.
string host_of(string_view host, string_view address) {
    auto lower = Converter::lower_case(host);

    if (lower.empty()) {
        throw invalid_argument("An address must name a host: " + string(address));
    }

    if (!is_host_name(lower) && !is_ipv6(lower)) {
        throw not_a_host(address);
    }

    return lower;
}

// The text of the port authority names after host, less the colon before it:
// empty when it names none. Url reads an IPv6 address out of brackets as if
// it were in them, and a name or an IPv4 address in them as if it were not,
// but only an IPv6 address is ever in them (RFC 3986 §3.2.2), so authority
// must name host as Url writes it. Throws invalid_argument, naming address,
// for an authority that does not.
string_view port_text_of(string_view authority, const string& host, string_view address) {
    Url host_only;

    host_only.set_host(host);
    host_only.set_path("");

    const auto written_host = host_only.to_string();

    if (Converter::lower_case(authority.substr(0, written_host.size())) != written_host) {
        throw not_a_host(address);
    }

    auto port = authority.substr(written_host.size());

    if (!port.empty()) {
        port.remove_prefix(1);
    }

    return port;
}

// The port text names, or http's when it is empty, as a URL's may be (RFC
// 3986 §3.2.3). Throws invalid_argument, naming address, for one that no
// node can be dialed at.
uint16_t port_of(string_view text, string_view address) {
    if (text.empty()) {
        return http_port;
    }

    const auto* const first = text.data();
    const auto* const last = next(first, static_cast<ptrdiff_t>(text.size()));
    uint16_t port = 0;
    const auto [stop, error] = from_chars(first, last, port);

    if (errc() != error || stop != last || 0 == port) {
        throw invalid_argument("An address's port must be a number from 1 to 65535: "
                               + string(address));
    }

    return port;
}

// address, an http URL naming a host, as it is dialed. Throws
// invalid_argument, saying why, for one that is not. Url gives a URL with no
// path the path "/".
string written(string_view address) {
    const auto authority = authority_of(address);
    const auto host = host_of(host_url_of(authority, address).host(), address);
    Url endpoint;

    endpoint.set_host(host);
    endpoint.set_port(port_of(port_text_of(authority, host, address), address));
    endpoint.set_path("");

    return string(http_scheme) + endpoint.to_string();
}

} // namespace

namespace protocol {

DialRequest DialRequest::parse(string_view text) {
    const auto json = Json::parse(text);
    const string key(address_key);

    if (!json.is_object() || !json.has(key) || !json.at(key).is_string()) {
        throw invalid_argument("A request must be a JSON object whose \"" + key
                               + "\" is a string");
    }

    return DialRequest(json.at(key).string());
}

DialRequest::DialRequest(string_view address)
    : m_endpoint(written(Converter::trimmed(address))) {}

const string& DialRequest::endpoint() const noexcept {
    return m_endpoint;
}

string DialRequest::to_string() const {
    return Json(Json::Object{{string(address_key), m_endpoint}}).dump();
}

} // namespace protocol
