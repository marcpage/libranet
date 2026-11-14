#include <gtest/gtest.h>
#include <stdexcept>
#include <string>
#include <vector>

#include "net/Address.hpp"

using net::Address;

namespace {

// Ports the tests make addresses with.
constexpr auto ssh_port = 22;
constexpr auto http_port = 80;
constexpr auto development_port = 3000;
constexpr auto alternate_http_port = 8080;
constexpr auto highest_port = 65535;

} // namespace

TEST(AddressTest, ConstructorsAndGetters) {
    // Standard constructor with IPv4 host and standard HTTP port
    const Address addr1("127.0.0.1", http_port);
    EXPECT_EQ(addr1.host(), "127.0.0.1");
    EXPECT_EQ(addr1.port(), http_port);

    // Standard constructor with hostname and high port boundary
    const Address addr2("localhost", highest_port);
    EXPECT_EQ(addr2.host(), "localhost");
    EXPECT_EQ(addr2.port(), highest_port);

    // Constructor with IPv6 host
    const Address addr3("::1", alternate_http_port);
    EXPECT_EQ(addr3.host(), "::1");
    EXPECT_EQ(addr3.port(), alternate_http_port);
}

TEST(AddressTest, ParsingIpv4AndHostnames) {
    const Address addr1 = Address::parse("192.168.1.1:8080");
    EXPECT_EQ(addr1.host(), "192.168.1.1");
    EXPECT_EQ(addr1.port(), 8080);

    const Address addr2 = Address::parse("example.com:443");
    EXPECT_EQ(addr2.host(), "example.com");
    EXPECT_EQ(addr2.port(), 443);

    const Address addr3 = Address::parse("0.0.0.0:0");
    EXPECT_EQ(addr3.host(), "0.0.0.0");
    EXPECT_EQ(addr3.port(), 0);
}

TEST(AddressTest, ParsingIpv6) {
    // Bracketed IPv6 address
    const Address addr1 = Address::parse("[::1]:80");
    EXPECT_EQ(addr1.host(), "::1");
    EXPECT_EQ(addr1.port(), 80);

    // Full IPv6 address with brackets
    const Address addr2 = Address::parse("[2001:db8::1]:8080");
    EXPECT_EQ(addr2.host(), "2001:db8::1");
    EXPECT_EQ(addr2.port(), 8080);
}

TEST(AddressTest, FormattingToString) {
    // Standard IPv4 to_string
    const Address addr1("127.0.0.1", alternate_http_port);
    EXPECT_EQ(addr1.to_string(), "127.0.0.1:8080");

    // Hostname to_string
    const Address addr2("localhost", development_port);
    EXPECT_EQ(addr2.to_string(), "localhost:3000");

    // IPv6 auto-wrapping brackets check
    const Address addr3("::1", http_port);
    EXPECT_EQ(addr3.to_string(), "[::1]:80");

    const Address addr4("fe80::1ff:fe23:4567:890a", ssh_port);
    EXPECT_EQ(addr4.to_string(), "[fe80::1ff:fe23:4567:890a]:22");
}

TEST(AddressTest, ParsingExceptions) {
    // Missing colon / port delimiter
    EXPECT_THROW((void)Address::parse("127.0.0.1"), std::invalid_argument);

    // Invalid non-numeric port
    EXPECT_THROW((void)Address::parse("127.0.0.1:abc"), std::invalid_argument);

    // Empty string
    EXPECT_THROW((void)Address::parse(""), std::invalid_argument);
}

TEST(AddressTest, Resolve) {
    // 1. Resolve loopback address
    const Address addr1("127.0.0.1", http_port);
    const std::vector<std::string> resolved_ips = addr1.resolve();

    // Should resolve to at least one entry (e.g., "127.0.0.1" or loopback representation)
    EXPECT_FALSE(resolved_ips.empty());

    // 2. Resolve localhost hostname
    const Address addr2("localhost", http_port);
    const std::vector<std::string> resolved_local = addr2.resolve();
    EXPECT_FALSE(resolved_local.empty());

    // 3. Test that resolving a non-existent or invalid host throws a runtime_error
    const Address addr3("this-host-definitely-does-not-exist-12345.invalid", http_port);
    EXPECT_THROW((void)addr3.resolve(), std::runtime_error);
}

TEST(AddressTest, GetLocalAddresses) {
    const std::vector<std::string> local_addrs = Address::get_local_addresses();

    // A machine should at least have loopback addresses (like 127.0.0.1 or ::1) active
    EXPECT_FALSE(local_addrs.empty());

    // Optional: Print discovered local interfaces during test run for visibility
    // std::cout << "\n    [Discovered local IPs: ";
    // for (const auto& ip : local_addrs) { std::cout << ip << " "; }
    // std::cout << "] " << std::flush;
}
