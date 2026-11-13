#include <iostream>
#include <cassert>
#include <string>
#include <vector>
#include <stdexcept>
#include "Address.hpp"

using namespace net;

void test_constructors_and_getters() {
    std::cout << "-> Testing constructors and basic getters... " << std::flush;

    // Standard constructor with IPv4 host and standard HTTP port
    Address addr1("127.0.0.1", 80);
    assert(addr1.host() == "127.0.0.1");
    assert(addr1.port() == 80);

    // Standard constructor with hostname and high port boundary
    Address addr2("localhost", 65535);
    assert(addr2.host() == "localhost");
    assert(addr2.port() == 65535);

    // Constructor with IPv6 host
    Address addr3("::1", 8080);
    assert(addr3.host() == "::1");
    assert(addr3.port() == 8080);

    std::cout << "PASSED\n";
}

void test_parsing_ipv4_and_hostnames() {
    std::cout << "-> Testing string parsing for IPv4 addresses and hostnames... " << std::flush;

    Address addr1 = Address::parse("192.168.1.1:8080");
    assert(addr1.host() == "192.168.1.1");
    assert(addr1.port() == 8080);

    Address addr2 = Address::parse("example.com:443");
    assert(addr2.host() == "example.com");
    assert(addr2.port() == 443);

    Address addr3 = Address::parse("0.0.0.0:0");
    assert(addr3.host() == "0.0.0.0");
    assert(addr3.port() == 0);

    std::cout << "PASSED\n";
}

void test_parsing_ipv6() {
    std::cout << "-> Testing string parsing for IPv6 addresses... " << std::flush;

    // Bracketed IPv6 address
    Address addr1 = Address::parse("[::1]:80");
    assert(addr1.host() == "::1");
    assert(addr1.port() == 80);

    // Full IPv6 address with brackets
    Address addr2 = Address::parse("[2001:db8::1]:8080");
    assert(addr2.host() == "2001:db8::1");
    assert(addr2.port() == 8080);

    std::cout << "PASSED\n";
}

void test_formatting_to_string() {
    std::cout << "-> Testing to_string() representation... " << std::flush;

    // Standard IPv4 to_string
    Address addr1("127.0.0.1", 8080);
    assert(addr1.to_string() == "127.0.0.1:8080");

    // Hostname to_string
    Address addr2("localhost", 3000);
    assert(addr2.to_string() == "localhost:3000");

    // IPv6 auto-wrapping brackets check
    Address addr3("::1", 80);
    assert(addr3.to_string() == "[::1]:80");

    Address addr4("fe80::1ff:fe23:4567:890a", 22);
    assert(addr4.to_string() == "[fe80::1ff:fe23:4567:890a]:22");

    std::cout << "PASSED\n";
}

void test_parsing_exceptions() {
    std::cout << "-> Testing parsing exception handling... " << std::flush;

    // Missing colon / port delimiter
    bool caught_missing_port = false;
    try {
        (void)Address::parse("127.0.0.1");
    } catch (const std::invalid_argument&) {
        caught_missing_port = true;
    }
    assert(caught_missing_port);

    // Invalid non-numeric port
    bool caught_invalid_port = false;
    try {
        (void)Address::parse("127.0.0.1:abc");
    } catch (const std::invalid_argument&) {
        caught_invalid_port = true;
    }
    assert(caught_invalid_port);

    // Empty string
    bool caught_empty = false;
    try {
        (void)Address::parse("");
    } catch (const std::invalid_argument&) {
        caught_empty = true;
    }
    assert(caught_empty);

    std::cout << "PASSED\n";
}

void test_resolve() {
    std::cout << "-> Testing address resolution (resolve())... " << std::flush;

    // 1. Resolve loopback address
    Address addr1("127.0.0.1", 80);
    std::vector<std::string> resolved_ips = addr1.resolve();

    // Should resolve to at least one entry (e.g., "127.0.0.1" or loopback representation)
    assert(!resolved_ips.empty());

    // 2. Resolve localhost hostname
    Address addr2("localhost", 80);
    std::vector<std::string> resolved_local = addr2.resolve();
    assert(!resolved_local.empty());

    // 3. Test that resolving a non-existent or invalid host throws a runtime_error
    bool caught_resolution_failure = false;
    try {
        Address addr3("this-host-definitely-does-not-exist-12345.invalid", 80);
        (void)addr3.resolve();
    } catch (const std::runtime_error&) {
        caught_resolution_failure = true;
    }
    assert(caught_resolution_failure);

    std::cout << "PASSED\n";
}

void test_get_local_addresses() {
    std::cout << "-> Testing local active address retrieval (get_local_addresses())... " << std::flush;

    std::vector<std::string> local_addrs = Address::get_local_addresses();

    // A machine should at least have loopback addresses (like 127.0.0.1 or ::1) active
    assert(!local_addrs.empty());

    // Optional: Print discovered local interfaces during test run for visibility
    // std::cout << "\n    [Discovered local IPs: ";
    // for (const auto& ip : local_addrs) { std::cout << ip << " "; }
    // std::cout << "] " << std::flush;

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "    Running Address Test Suite      \n";

    test_constructors_and_getters();
    test_parsing_ipv4_and_hostnames();
    test_parsing_ipv6();
    test_formatting_to_string();
    test_parsing_exceptions();
    test_resolve();
    test_get_local_addresses(); // Include new local addresses test routine[cite: 5]

    std::cout << "   All Tests Passed Successfully!   \n";

    return 0;
}
