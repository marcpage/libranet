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

#include "net/IpAddress.hpp"
#include "types/Converter.hpp"
#include "types/Json.hpp"

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

struct HostAndPort {
    string_view host; // With its brackets, for an IPv6 address
    string_view port; // Empty when none is named
};

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

// The host and port authority names. A colon in brackets is part of an IPv6
// address, not the start of a port (RFC 3986 §3.2.2).
HostAndPort host_and_port(string_view authority) {
    const auto colon = authority.rfind(':');
    const auto bracket = authority.rfind(']');

    if (string_view::npos == colon || (bracket != string_view::npos && colon < bracket)) {
        return {authority, {}};
    }

    return {authority.substr(0, colon), authority.substr(colon + 1)};
}

// Whether host is a name or an IPv4 address, of the characters RFC 3986
// §2.3 leaves unreserved, as a node list's endpoint names one.
bool is_host_name(string_view host) {
    constexpr string_view unreserved = "-._~";

    return !host.empty() && all_of(host.begin(), host.end(), [unreserved](char character) {
        return isalnum(static_cast<unsigned char>(character)) != 0
               || unreserved.find(character) != string_view::npos;
    });
}

// Whether host is an IPv6 address in brackets. An IPv4 address is never in
// them (RFC 3986 §3.2.2).
bool is_bracketed_ipv6(string_view host) {
    if (host.size() < 2 || '[' != host.front() || ']' != host.back()) {
        return false;
    }

    const auto address = host.substr(1, host.size() - 2);

    return address.find(':') != string_view::npos && IpAddress::parse(address).has_value();
}

// host, as an address names it, as it is dialed: in lower case. Throws
// invalid_argument, naming address, for a host that is not one.
string host_of(string_view host, string_view address) {
    auto lower = Converter::lower_case(host);

    if (lower.empty()) {
        throw invalid_argument("An address must name a host: " + string(address));
    }

    if (!is_host_name(lower) && !is_bracketed_ipv6(lower)) {
        throw invalid_argument("An address must name its host by a name or an IP address, "
                               "an IPv6 one in brackets: "
                               + string(address));
    }

    return lower;
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
// invalid_argument, saying why, for one that is not.
string written(string_view address) {
    const auto [host, port] = host_and_port(authority_of(address));

    return string(http_scheme) + host_of(host, address) + ':'
           + std::to_string(port_of(port, address));
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
