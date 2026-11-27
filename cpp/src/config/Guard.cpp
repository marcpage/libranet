#include "Guard.hpp"

#include <array>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "Credential.hpp"
#include "http/BasicCredentials.hpp"
#include "http/Headers.hpp"
#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "http/Url.hpp"
#include "logger/Logger.hpp"
#include "net/IpAddress.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"
#include "web/ConnectionInfo.hpp"
#include "web/SiteChecks.hpp"

using config::Credential;
using config::Guard;
using http::BasicCredentials;
using http::Headers;
using http::Problem;
using http::Request;
using http::Response;
using http::Url;
using net::IpAddress;
using std::nullopt;
using std::optional;
using std::string;
using std::string_view;
using std::to_string;
using std::vector;
using types::config_api_path;
using types::config_path;
using types::Converter;
using web::ConnectionInfo;
using web::SiteChecks;

namespace {

using Handler = Guard::Handler;
using Status = http::ResponseCode::Status;

constexpr string_view referer_header = "Referer";

// What a browser sends with a page another page of the same site opens in a
// window of its own (W3C Fetch Metadata Request Headers): a page of the same
// site, not of the same origin; a page loaded, not a script's request; and
// into a window, not a frame, where another page could draw over it.
// Sec-Fetch-User, which says the person using the browser asked for it, is
// not among them, as WebKit never sends it.
constexpr string_view fetch_mode_header = "Sec-Fetch-Mode";
constexpr string_view fetch_dest_header = "Sec-Fetch-Dest";
constexpr string_view same_site = "same-site";
constexpr string_view navigate = "navigate";
constexpr string_view document = "document";

// The headers that grant another origin's page a request of /config (the
// WHATWG Fetch Standard's CORS protocol, and W3C Private Network Access),
// none of which a /config answer carries (HTTP API §2.3.3).
constexpr std::array cross_origin_grants{
    "Access-Control-Allow-Origin",
    "Access-Control-Allow-Credentials",
    "Access-Control-Allow-Methods",
    "Access-Control-Allow-Headers",
    "Access-Control-Expose-Headers",
    "Access-Control-Max-Age",
    "Access-Control-Allow-Private-Network",
};

// Whether the client that sent a request on connection is on this machine: it
// came from a loopback address, or from the address of this machine it
// reached, which a client elsewhere cannot come from, since the replies to it
// never leave this machine (HTTP API §2.3). An IPv4-mapped address is the
// IPv4 address it maps, and one that is no IP address is no client's here.
bool is_local(const ConnectionInfo& connection) {
    const auto source = IpAddress::parse(connection.remote_address().host());

    if (!source) {
        return false;
    }

    if (source->is_loopback()) {
        return true;
    }

    const auto reached = IpAddress::parse(connection.local_address().host());

    return reached && reached->to_string() == source->to_string();
}

// Whether path, percent-decoded, without a query, is /config/api, or beneath
// it.
bool is_beneath_api(const string& path) {
    const string api(config_api_path);

    return path == api || 0 == path.rfind(api + '/', 0);
}

// Whether uri names /config/api, or what is beneath it, as a request's target
// gives it.
bool names_config_api(string_view uri) {
    const auto path = uri.substr(0, uri.find('?'));

    return is_beneath_api(Url::decode(path, Url::EncodeContext::UriComponent));
}

// Whether path, percent-decoded, is a page of /config: /config, or a path
// beneath it outside /config/api (HTTP API §2.5).
bool is_config_page(const string& path) {
    const string config(config_path);

    return (path == config || 0 == path.rfind(config + '/', 0)) && !is_beneath_api(path);
}

// Whether request's header name says value, whatever its case and the
// whitespace around it.
bool says(const Request& request, string_view name, string_view value) {
    const auto said = request.headers().get(name);

    return said && value == Converter::lower_case(Headers::trim_whitespace(*said));
}

// Whether request is for a page of /config that a page of the same site, such
// as the root application's on the main port, opens in a window of its own: a
// GET outside /config/api (HTTP API §2.3.3). The page that opens it is of
// another origin, which can neither read it nor drive it.
bool is_opened_from_same_site(const Request& request) {
    return "GET" == request.method() && !names_config_api(request.uri())
           && says(request, SiteChecks::site_header, same_site)
           && says(request, fetch_mode_header, navigate)
           && says(request, fetch_dest_header, document);
}

// The page referer, as a Referer header gives it, names: an address of a page
// served over HTTP. Nothing when it names none.
optional<Url> page_named_by(string_view referer) {
    auto page = Url::parse(Headers::trim_whitespace(referer));

    if (!page || page->host().empty()) {
        return nullopt;
    }

    const auto scheme = Converter::lower_case(page->scheme());

    if ("http" != scheme && "https" != scheme) {
        return nullopt;
    }

    return page;
}

// Whether page is at the host and port host, as a Host header gives it,
// names. A port host leaves out is the default of page's scheme. The scheme
// is not compared, since host carries none.
bool is_at(const Url& page, string_view host) {
    auto here = Url::parse(Headers::trim_whitespace(host));

    if (!here || !here->scheme().empty() || here->host().empty()) {
        return false;
    }

    here->set_scheme(string(page.scheme()));
    return Converter::lower_case(page.host()) == Converter::lower_case(here->host())
           && page.effective_port() == here->effective_port();
}

// The host and port of page, as a refusal names them. Never its path, which
// could be a page's the operator would not have repeated.
string host_and_port(const Url& page) {
    const auto host = string(page.host());
    const auto port = to_string(page.effective_port().value_or(0));

    return (string::npos == host.find(':') ? host : "[" + host + "]") + ":" + port;
}

// A refusal of a request beneath /config/api, for reason.
SiteChecks::Refusal referer_refused(const string& reason) {
    auto detail = "/config/api is served only to the /config application's pages" + reason;

    return {referer_header, std::move(detail)};
}

// Why request, beneath /config/api, is from no page of /config, as its
// Referer names it at the host and port its Host names (HTTP API §2.5); or
// nothing, when it is from one. A script names the page it stands in for, as
// curl -e does, so this is not a protection: it keeps out a request that no
// page of /config made.
optional<SiteChecks::Refusal> referer_refusal(const Request& request) {
    const auto& headers = request.headers();
    const auto referer = headers.get(referer_header);

    if (!referer) {
        return referer_refused(": the request carries no Referer.");
    }

    const auto page = page_named_by(*referer);

    if (!page) {
        return referer_refused(": the Referer is not an address of a page served over HTTP.");
    }

    const auto host = headers.get(SiteChecks::host_header).value_or("");

    if (!is_at(*page, host)) {
        return referer_refused(": the Referer names a page at " + host_and_port(*page)
                               + ", and the Host header names '" + string(host) + "'.");
    }

    if (!is_config_page(page->path())) {
        return referer_refused("; the Referer names a page outside it.");
    }

    return nullopt;
}

// Has response carry Guard::policy, and no header granting another origin's
// page a request (HTTP API §2.3.3).
void keep_to_own_origin(Response& response) {
    auto& headers = response.headers();

    for (const auto* const name : cross_origin_grants) {
        headers.remove(name);
    }

    headers.set(string(Guard::policy_header), string(Guard::policy));
}

// Answers 403 for a request from a client elsewhere (HTTP API §2.3).
void refuse_elsewhere(Response& response) {
    Problem(Status::Forbidden, "/config is served only to clients on this machine.")
        .answer(response);
}

// Answers 401, with the challenge that has a browser ask for a username and
// password (RFC 7617 §2, §2.1).
void challenge(Response& response) {
    Problem(Problem::Type::CredentialRequired, Status::Unauthorized,
            "/config requires the username and password this node captured.")
        .answer(response);
    response.headers().set("WWW-Authenticate",
                           R"(Basic realm=")" + string(Guard::realm) + R"(", charset="UTF-8")");
}

// Why request is taken to be another site's page's, as checks say, or no page
// of /config's when it is beneath /config/api; or nothing, when it is neither
// (HTTP API §2.3.3, §2.5). A page another page of the same site opens passes
// the checks the rest must, but for Host's.
optional<SiteChecks::Refusal> refusal(const SiteChecks& checks, const Request& request,
                                      const ConnectionInfo& connection) {
    if (auto refused = checks.host_refusal(request, connection)) {
        return refused;
    }

    if (is_opened_from_same_site(request)) {
        return nullopt;
    }

    if (auto refused = checks.page_refusal(request)) {
        return refused;
    }

    return names_config_api(request.uri()) ? referer_refusal(request) : nullopt;
}

// Whether request gives credential, capturing it when there is none yet. A
// field that cannot be read gives none, so that it never captures one.
bool authenticates(const Credential& credential, const Request& request) {
    const auto field = request.headers().get(BasicCredentials::field_name);

    if (!field) {
        return false;
    }

    const auto credentials = BasicCredentials::parse(*field);

    return credentials && credential.authenticate(credentials->user_pass());
}

// Has handler answer request, if it is from a client on this machine, from
// none of another site's pages, as checks say, and gives credential.
void serve(const Credential& credential, const SiteChecks& checks, const Handler& handler,
           const Request& request, Response& response, ConnectionInfo& connection) {
    if (!is_local(connection)) {
        lDebug << "Refused" << request.method() << request.uri() << "from"
               << connection.remote_address().to_string() + ":"
               << "not a client on this machine";
        refuse_elsewhere(response);
        return;
    }

    if (auto refused = refusal(checks, request, connection)) {
        lWarn << "Refused" << request.method() << request.uri() << "for its" << refused->header
              << "header:" << refused->reason;
        Problem(Status::Forbidden, std::move(refused->reason)).answer(response);
        return;
    }

    if (!authenticates(credential, request)) {
        challenge(response);
        return;
    }

    handler(request, response, connection);
}

} // namespace

namespace config {

Guard::Guard(Credential credential, vector<string> hosts)
    : m_credential(std::move(credential))
    , m_checks(std::move(hosts), string(config_path)) {}

Guard::Handler Guard::in_front_of(Handler handler) const {
    return [credential = m_credential, checks = m_checks, handler = std::move(handler)](
               const Request& request, Response& response, ConnectionInfo& connection) {
        serve(credential, checks, handler, request, response, connection);
        keep_to_own_origin(response);
    };
}

} // namespace config
