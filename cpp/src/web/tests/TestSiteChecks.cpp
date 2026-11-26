#include <gtest/gtest.h>
#include <array>
#include <cstddef>
#include <initializer_list>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <unistd.h>

#include "http/Request.hpp"
#include "net/Address.hpp"
#include "types/Converter.hpp"
#include "web/ConnectionInfo.hpp"
#include "web/SiteChecks.hpp"

using http::Request;
using net::Address;
using types::Converter;
using web::ConnectionInfo;
using web::SiteChecks;

using namespace std::string_literals;

namespace {

using Header = std::pair<std::string, std::string>;

// Where a client on this machine comes from, and the port /config's
// requests reach.
constexpr std::string_view loopback = "127.0.0.1";
constexpr Address::Port client_port = 50'000;
constexpr Address::Port config_port = 8'180;

// Addresses of this machine a connection may reach, set aside for
// documentation (RFC 5737, RFC 3849).
constexpr std::string_view reached_ipv4 = "192.0.2.7";
constexpr std::string_view reached_ipv6 = "2001:db8::7";

// As long as a host name may be on macOS (MAXHOSTNAMELEN).
constexpr std::size_t machine_name_size = 256;

// The hosts /config is served as by default (HTTP API §2.3.3), but for the
// name this machine gives itself, which SiteChecks adds.
SiteChecks default_checks() {
    return {{"localhost", "127.0.0.1", "::1"}, "/config"};
}

// A request carrying headers, and no others.
Request with_headers(std::initializer_list<Header> headers) {
    Request request;

    request.set_method("GET");
    request.set_uri("/config/");

    for (const auto& [name, value] : headers) {
        request.headers().set(name, value);
    }

    return request;
}

// A connection from this machine that reached it at host.
ConnectionInfo reached_at(std::string_view host) {
    return {Address(loopback, client_port), Address(host, config_port)};
}

// The header checks refuses a request carrying headers for, on a
// connection that reached host, or "" if they pass it.
std::string refused_for(const SiteChecks& checks, std::initializer_list<Header> headers,
                        std::string_view host = loopback) {
    const auto refusal = checks.refusal(with_headers(headers), reached_at(host));

    return refusal.has_value() ? std::string(refusal->header) : "";
}

// The header the default checks refuse a request carrying headers for.
std::string refused_for(std::initializer_list<Header> headers) {
    return refused_for(default_checks(), headers);
}

// Why the default checks refuse a request carrying headers, or "".
std::string reason_for(std::initializer_list<Header> headers) {
    const auto refusal = default_checks().refusal(with_headers(headers), reached_at(loopback));

    return refusal.has_value() ? refusal->reason : "";
}

// The name this machine gives itself.
std::string machine_name() {
    std::array<char, machine_name_size + 1> name{};

    if (0 != ::gethostname(name.data(), machine_name_size)) {
        throw std::runtime_error("Cannot read this machine's name");
    }

    return name.data();
}

// Whether the default checks serve as the host that authority names.
bool serves_as(std::string_view authority) {
    return default_checks().serves_as(authority);
}

} // namespace

// A script or a command-line client sends none of them.
TEST(SiteChecksTest, NoneOfTheHeadersPasses) {
    EXPECT_EQ(refused_for({}), "");
    EXPECT_EQ(refused_for({{"Referer", "http://attacker.example/"}}), "");
}

TEST(SiteChecksTest, HostServedAsPassesWhateverItsPort) {
    EXPECT_TRUE(serves_as("localhost:8180"));
    EXPECT_TRUE(serves_as("localhost"));
    EXPECT_TRUE(serves_as("127.0.0.1:8180"));
    EXPECT_TRUE(serves_as("[::1]:8180"));
    EXPECT_TRUE(serves_as("[::1]"));
    EXPECT_TRUE(serves_as(" localhost:8180 "));
}

TEST(SiteChecksTest, HostIsMatchedWhateverItsCase) {
    const SiteChecks checks({"Node.Example"}, "/config");

    EXPECT_TRUE(serves_as("LocalHost:8180"));
    EXPECT_TRUE(checks.serves_as("node.example:8180"));
    EXPECT_TRUE(checks.serves_as("NODE.EXAMPLE"));
}

TEST(SiteChecksTest, HostsAreShellStylePatterns) {
    const SiteChecks checks({"*.example", "node?.test", "[ab].lan", "back\\slash"}, "/config");

    EXPECT_TRUE(checks.serves_as("node.example:8180"));
    EXPECT_TRUE(checks.serves_as("a.b.example"));
    EXPECT_FALSE(checks.serves_as("example"));
    EXPECT_TRUE(checks.serves_as("node1.test"));
    EXPECT_FALSE(checks.serves_as("node12.test"));
    EXPECT_TRUE(checks.serves_as("b.lan"));
    EXPECT_FALSE(checks.serves_as("c.lan"));
    EXPECT_TRUE(checks.serves_as("back\\slash"));
    EXPECT_FALSE(checks.serves_as("backslash"));
}

TEST(SiteChecksTest, HostOfAnotherNameIsRefused) {
    EXPECT_EQ(refused_for({{"Host", "attacker.example:8180"}}), "Host");
    EXPECT_EQ(refused_for({{"Host", "localhost.attacker.example"}}), "Host");
    EXPECT_EQ(refused_for({{"Host", "192.0.2.1:8180"}}), "Host");
    EXPECT_EQ(refused_for({{"Host", ""}}), "Host");
}

// What follows a NUL would go unread, were it read as a C string.
TEST(SiteChecksTest, HostHoldingANulIsRefused) {
    const auto host = "localhost\0.attacker.example"s;

    EXPECT_FALSE(serves_as(host));
    EXPECT_EQ(refused_for({{"Host", host}}), "Host");
}

TEST(SiteChecksTest, HostWhoseBracketsAreNotClosedIsRefused) {
    const SiteChecks checks({"*"}, "/config");

    EXPECT_TRUE(checks.serves_as("anything.example"));
    EXPECT_FALSE(checks.serves_as("[::1:8180"));
    EXPECT_FALSE(checks.serves_as(":8180"));
}

TEST(SiteChecksTest, ThisMachinesOwnNameIsServedAs) {
    const auto name = machine_name();
    const SiteChecks checks({}, "/config");

    ASSERT_FALSE(name.empty());
    EXPECT_TRUE(checks.serves_as(name + ":8180"));
    EXPECT_TRUE(checks.serves_as(Converter::lower_case(name)));
    EXPECT_FALSE(checks.serves_as(name + ".attacker.example"));
}

// A site can point a name at this machine, but not an address.
TEST(SiteChecksTest, HostNamingTheAddressReachedPasses) {
    const SiteChecks checks({}, "/config");

    EXPECT_EQ(refused_for(checks, {{"Host", "192.0.2.7:8180"}}, reached_ipv4), "");
    EXPECT_EQ(refused_for(checks, {{"Host", "[::ffff:192.0.2.7]:8180"}}, reached_ipv4), "");
    EXPECT_EQ(refused_for(checks, {{"Host", "[2001:DB8:0::7]:8180"}}, reached_ipv6), "");
    EXPECT_EQ(refused_for(checks, {{"Host", "192.0.2.7:8180"}}, "::ffff:192.0.2.7"), "");
}

TEST(SiteChecksTest, HostNamingAnotherAddressIsRefused) {
    const SiteChecks checks({}, "/config");

    EXPECT_EQ(refused_for(checks, {{"Host", "192.0.2.8:8180"}}, reached_ipv4), "Host");
    EXPECT_EQ(refused_for(checks, {{"Host", "[2001:db8::8]"}}, reached_ipv6), "Host");
    EXPECT_EQ(refused_for(checks, {{"Host", "127.0.0.1"}}, reached_ipv4), "Host");
    EXPECT_EQ(refused_for(checks, {{"Host", "rebound.example"}}, loopback), "Host");
}

TEST(SiteChecksTest, HostRefusalNamesTheHostAndWhatIsServed) {
    EXPECT_EQ(reason_for({{"Host", "attacker.example:8180"}}),
              "/config is not served as 'attacker.example:8180', which the Host header names "
              "(it is served as the address it is reached at, this machine's own name, and as "
              "network.config_hosts lists).");
}

TEST(SiteChecksTest, HostToServeAsHoldingANulIsTurnedDown) {
    const std::vector<std::string> hosts{"localhost", "node\0.example"s};

    // Mistaken: it is made only to see that making it throws.
    // cppcheck-suppress unusedScopedObject
    EXPECT_THROW(SiteChecks(hosts, "/config"), std::invalid_argument);
}

TEST(SiteChecksTest, OwnSiteOrNonePasses) {
    EXPECT_EQ(refused_for({{"Sec-Fetch-Site", "same-origin"}}), "");
    EXPECT_EQ(refused_for({{"Sec-Fetch-Site", "none"}}), "");
    EXPECT_EQ(refused_for({{"Sec-Fetch-Site", " Same-Origin "}}), "");
    EXPECT_EQ(refused_for({{"Sec-Fetch-Site", "NONE"}}), "");
}

// Another port on this machine is the same site, and another origin.
TEST(SiteChecksTest, AnotherSiteIsRefused) {
    EXPECT_EQ(refused_for({{"Sec-Fetch-Site", "same-site"}}), "Sec-Fetch-Site");
    EXPECT_EQ(refused_for({{"Sec-Fetch-Site", "cross-site"}}), "Sec-Fetch-Site");
    EXPECT_EQ(refused_for({{"Sec-Fetch-Site", ""}}), "Sec-Fetch-Site");
    EXPECT_EQ(refused_for({{"Sec-Fetch-Site", "same-origin, none"}}), "Sec-Fetch-Site");
}

TEST(SiteChecksTest, SiteRefusalNamesWhatItSays) {
    EXPECT_EQ(reason_for({{"Sec-Fetch-Site", "cross-site"}}),
              "/config is served only to this node's own pages; Sec-Fetch-Site is "
              "'cross-site'.");
}

// Origin is looked at only where a browser sends no Sec-Fetch-Site.
TEST(SiteChecksTest, SiteDecidesOverOrigin) {
    EXPECT_EQ(refused_for({{"Host", "localhost:8180"},
                           {"Sec-Fetch-Site", "same-origin"},
                           {"Origin", "http://attacker.example"}}),
              "");
    EXPECT_EQ(refused_for({{"Host", "localhost:8180"},
                           {"Sec-Fetch-Site", "cross-site"},
                           {"Origin", "http://localhost:8180"}}),
              "Sec-Fetch-Site");
}

TEST(SiteChecksTest, OriginOfTheHostAndPortPasses) {
    EXPECT_EQ(refused_for({{"Host", "localhost:8180"}, {"Origin", "http://localhost:8180"}}), "");
    EXPECT_EQ(refused_for({{"Host", "LocalHost:8180"}, {"Origin", "http://localhost:8180"}}), "");
    EXPECT_EQ(refused_for({{"Host", "[::1]:8180"}, {"Origin", "http://[::1]:8180"}}), "");
}

TEST(SiteChecksTest, OriginOfAnotherHostOrPortIsRefused) {
    EXPECT_EQ(refused_for({{"Host", "localhost:8180"}, {"Origin", "http://localhost:8080"}}),
              "Origin");
    EXPECT_EQ(refused_for({{"Host", "localhost:8180"}, {"Origin", "http://127.0.0.1:8180"}}),
              "Origin");
    EXPECT_EQ(refused_for({{"Host", "localhost:8180"}, {"Origin", "localhost:8180"}}),
              "Origin");
    EXPECT_EQ(refused_for({{"Origin", "http://localhost:8180"}}), "Origin");
}

// "null" names no origin, so it is the same as none.
TEST(SiteChecksTest, NullOriginIsRefused) {
    EXPECT_EQ(refused_for({{"Host", "localhost:8180"}, {"Origin", "null"}}),
              "Origin");
    EXPECT_EQ(refused_for({{"Host", ""}, {"Origin", "http://"}}), "Host");
}

TEST(SiteChecksTest, OriginRefusalNamesOriginAndHost) {
    EXPECT_EQ(reason_for({{"Host", "localhost:8180"}, {"Origin", "null"}}),
              "/config is served only to this node's own pages; Origin is 'null' and Host is "
              "'localhost:8180'.");
}

TEST(SiteChecksTest, HostIsCheckedFirst) {
    EXPECT_EQ(refused_for({{"Host", "attacker.example"}, {"Sec-Fetch-Site", "cross-site"}}),
              "Host");
}

// So that a check of the pages can be passed over, as /config does for a
// page another of this site opens, while Host is still checked.
TEST(SiteChecksTest, EachCheckIsMadeAlone) {
    const auto checks = default_checks();
    const auto request = with_headers({
        {"Host", "attacker.example"},
        {"Sec-Fetch-Site", "same-site"},
    });
    const auto host_refusal = checks.host_refusal(request, reached_at(loopback));
    const auto page_refusal = checks.page_refusal(request);

    ASSERT_TRUE(host_refusal.has_value());
    EXPECT_EQ(host_refusal->header, "Host");
    ASSERT_TRUE(page_refusal.has_value());
    EXPECT_EQ(page_refusal->header, "Sec-Fetch-Site");
    EXPECT_FALSE(checks.page_refusal(with_headers({{"Host", "attacker.example"}})).has_value());
}
