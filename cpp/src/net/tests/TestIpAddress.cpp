#include <gtest/gtest.h>
#include <string>
#include <string_view>

#include "net/IpAddress.hpp"

using net::IpAddress;

namespace {

// The address text names, written back; empty when it names none.
std::string reread(std::string_view text) {
    const auto address = IpAddress::parse(text);

    return address ? address->to_string() : "";
}

// The address text names, as a URL's host; empty when it names none.
std::string url_host(std::string_view text) {
    const auto address = IpAddress::parse(text);

    return address ? address->url_host() : "";
}

// Whether text names a loopback address.
bool is_loopback(std::string_view text) {
    const auto address = IpAddress::parse(text);

    return address && address->is_loopback();
}

} // namespace

TEST(IpAddressTest, ReadsIpv4) {
    EXPECT_EQ(reread("203.0.113.42"), "203.0.113.42");
    EXPECT_EQ(reread("0.0.0.0"), "0.0.0.0");
    EXPECT_EQ(reread("255.255.255.255"), "255.255.255.255");
}

// RFC 5952: as short as it can be, in lower case.
TEST(IpAddressTest, ReadsIpv6AndWritesItsShortestForm) {
    EXPECT_EQ(reread("2001:DB8:0:0:0:0:0:1"), "2001:db8::1");
    EXPECT_EQ(reread("::1"), "::1");
    EXPECT_EQ(reread("::"), "::");
    EXPECT_EQ(reread("fe80::1:2"), "fe80::1:2");
}

// A dual-stack listener reports an IPv4 client this way.
TEST(IpAddressTest, Ipv4MappedIsItsIpv4Address) {
    EXPECT_EQ(reread("::ffff:203.0.113.42"), "203.0.113.42");
    EXPECT_EQ(reread("::FFFF:cb00:712a"), "203.0.113.42");
    EXPECT_EQ(reread("::ffff:127.0.0.1"), "127.0.0.1");
}

TEST(IpAddressTest, TextThatNamesNoAddressIsRefused) {
    EXPECT_EQ(reread(""), "");
    EXPECT_EQ(reread("localhost"), "");
    EXPECT_EQ(reread("example.com"), "");
    EXPECT_EQ(reread("127.1"), "");
    EXPECT_EQ(reread("256.0.0.1"), "");
    EXPECT_EQ(reread("1.2.3.4.5"), "");
    EXPECT_EQ(reread(" 1.2.3.4"), "");
    EXPECT_EQ(reread("[::1]"), "");
    EXPECT_EQ(reread("::1::2"), "");
    EXPECT_EQ(reread("fe80::1%eth0"), "");
    EXPECT_EQ(reread(std::string("1.2.3.4\0", 8)), "");
    EXPECT_EQ(reread(std::string("::1\0x", 5)), "");
}

TEST(IpAddressTest, LoopbackAddresses) {
    EXPECT_TRUE(is_loopback("127.0.0.1"));
    EXPECT_TRUE(is_loopback("127.255.255.254"));
    EXPECT_TRUE(is_loopback("::1"));
    EXPECT_TRUE(is_loopback("::ffff:127.0.0.1"));
}

TEST(IpAddressTest, AddressesThatAreNotLoopback) {
    EXPECT_FALSE(is_loopback("126.255.255.255"));
    EXPECT_FALSE(is_loopback("128.0.0.1"));
    EXPECT_FALSE(is_loopback("0.0.0.0"));
    EXPECT_FALSE(is_loopback("203.0.113.42"));
    EXPECT_FALSE(is_loopback("::"));
    EXPECT_FALSE(is_loopback("::2"));
    EXPECT_FALSE(is_loopback("::ffff:203.0.113.42"));
}

// RFC 3986 §3.2.2: an IPv6 address is in brackets in a URL.
TEST(IpAddressTest, UrlHost) {
    EXPECT_EQ(url_host("203.0.113.42"), "203.0.113.42");
    EXPECT_EQ(url_host("2001:DB8::1"), "[2001:db8::1]");
    EXPECT_EQ(url_host("::ffff:203.0.113.42"), "203.0.113.42");
}
