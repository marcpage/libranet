#include <gtest/gtest.h>
#include <stdexcept>
#include <string>
#include <string_view>

#include "protocol/DialRequest.hpp"
#include "protocol/NodeList.hpp"
#include "storage/Identifier.hpp"

using protocol::DialRequest;
using protocol::NodeList;
using storage::Identifier;

namespace {

// The SHA-256 of nothing (FIPS 180-2), as a node's id.
constexpr std::string_view node_id =
    "sha256/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

// The endpoint address is dialed at.
std::string dialed(std::string_view address) {
    return DialRequest(address).endpoint();
}

// Why DialRequest turns address down, or "" when it does not.
std::string refusal_of(std::string_view address) {
    try {
        (void)DialRequest(address);
    } catch (const std::invalid_argument& error) {
        return error.what();
    }

    return "";
}

// Whether DialRequest turns address down.
bool refused(std::string_view address) {
    return !refusal_of(address).empty();
}

// Why DialRequest::parse() turns text down, or "" when it does not.
std::string parse_refusal_of(std::string_view text) {
    try {
        (void)DialRequest::parse(text);
    } catch (const std::invalid_argument& error) {
        return error.what();
    }

    return "";
}

} // namespace

// As the Python node writes each, in protocol/dial_requests.py.
TEST(DialRequestTest, AddressIsDialedAsOneEndpointHoweverItIsWritten) {
    EXPECT_EQ(dialed("http://192.168.1.10:8080"), "http://192.168.1.10:8080");
    EXPECT_EQ(dialed("http://Node.Example:18400/"), "http://node.example:18400");
    EXPECT_EQ(dialed("  http://192.168.1.10:8080/some/path?query  "), "http://192.168.1.10:8080");
    EXPECT_EQ(dialed("http://192.168.1.10"), "http://192.168.1.10:80");
    EXPECT_EQ(dialed("HTTP://192.168.1.10:8080"), "http://192.168.1.10:8080");
    EXPECT_EQ(dialed("http://[FE80::1]:8080"), "http://[fe80::1]:8080");
}

TEST(DialRequestTest, WhatFollowsTheHostAndPortIsLeftOut) {
    EXPECT_EQ(dialed("http://192.168.1.10:8080#fragment"), "http://192.168.1.10:8080");
    EXPECT_EQ(dialed("http://192.168.1.10?query"), "http://192.168.1.10:80");
    EXPECT_EQ(dialed("http://user:secret@node.example:8080"), "http://node.example:8080");
}

TEST(DialRequestTest, EmptyPortIs80) {
    EXPECT_EQ(dialed("http://192.168.1.10:"), "http://192.168.1.10:80");
    EXPECT_EQ(dialed("http://[::1]:"), "http://[::1]:80");
}

TEST(DialRequestTest, PortIsWrittenWithoutLeadingZeros) {
    EXPECT_EQ(dialed("http://localhost:0080"), "http://localhost:80");
}

TEST(DialRequestTest, AddressTheNodeCannotDialIsRefused) {
    EXPECT_TRUE(refused("192.168.1.10:8080"));
    EXPECT_TRUE(refused("https://192.168.1.10:8443"));
    EXPECT_TRUE(refused("ftp://example"));
    EXPECT_TRUE(refused("http:192.168.1.10:8080"));
    EXPECT_TRUE(refused("http://"));
    EXPECT_TRUE(refused("http://:8080"));
    EXPECT_TRUE(refused("http://192.168.1.10:99999"));
    EXPECT_TRUE(refused("http://192.168.1.10:0"));
    EXPECT_TRUE(refused("http://192.168.1.10:port"));
    EXPECT_TRUE(refused("http://192.168.1.10:80a"));
    EXPECT_TRUE(refused("http://192.168.1.10:+80"));
    EXPECT_TRUE(refused("http://192.168.1.10:-80"));
    EXPECT_TRUE(refused("http://[fe80::1"));
    EXPECT_TRUE(refused("http://fe80::1:8080"));
    EXPECT_TRUE(refused("http://[192.168.1.10]:8080"));
    EXPECT_TRUE(refused("http://[fe80::1%25eth0]:8080"));
    EXPECT_TRUE(refused("http://[fe80::1]x:8080"));
    EXPECT_TRUE(refused("http://node example:8080"));
    EXPECT_TRUE(refused("http://node.example:80:80"));
    EXPECT_TRUE(refused("   "));
    EXPECT_TRUE(refused(""));
}

TEST(DialRequestTest, RefusalSaysWhy) {
    EXPECT_EQ(refusal_of("192.168.1.10:8080"),
              "An address must start with http://, as http://192.168.1.10:8080 does: "
              "192.168.1.10:8080");
    EXPECT_EQ(refusal_of("http://:8080"), "An address must name a host: http://:8080");
    EXPECT_EQ(refusal_of("http://node example"),
              "An address must name its host by a name or an IP address, an IPv6 one in "
              "brackets: http://node example");
    EXPECT_EQ(refusal_of("http://192.168.1.10:99999"),
              "An address's port must be a number from 1 to 65535: http://192.168.1.10:99999");
}

// HttpApi §10.6.
TEST(DialRequestTest, EndpointIsOneANodeListNames) {
    const auto node = Identifier::parse(node_id);

    for (const auto* const address :
         {"http://Node.Example:18400/path", "http://[FE80::1]", "http://192.168.1.10:"}) {
        const DialRequest request(address);
        const NodeList list(node, {request.endpoint()});

        EXPECT_EQ(list.entries().at(0).endpoint, request.endpoint());
    }
}

TEST(DialRequestTest, RequestIsReadFromJson) {
    const auto request = DialRequest::parse(R"({"address": " http://Node.Example:8080/ "})");

    EXPECT_EQ(request.endpoint(), "http://node.example:8080");
}

TEST(DialRequestTest, OtherMembersAreLeftOut) {
    const auto request =
        DialRequest::parse(R"({"node_id": "sha256/abc", "address": "http://192.168.1.10"})");

    EXPECT_EQ(request.endpoint(), "http://192.168.1.10:80");
}

TEST(DialRequestTest, RequestThatIsNotAnObjectWithAStringAddressIsRefused) {
    const std::string not_a_request =
        R"(A request must be a JSON object whose "address" is a string)";

    EXPECT_EQ(parse_refusal_of(R"({"address": 8080})"), not_a_request);
    EXPECT_EQ(parse_refusal_of(R"({"address": null})"), not_a_request);
    EXPECT_EQ(parse_refusal_of(R"({"Address": "http://192.168.1.10:8080"})"), not_a_request);
    EXPECT_EQ(parse_refusal_of("{}"), not_a_request);
    EXPECT_EQ(parse_refusal_of(R"(["http://192.168.1.10:8080"])"), not_a_request);
    EXPECT_EQ(parse_refusal_of(R"("http://192.168.1.10:8080")"), not_a_request);
    EXPECT_FALSE(parse_refusal_of("http://192.168.1.10:8080").empty());
    EXPECT_FALSE(parse_refusal_of("").empty());
}

TEST(DialRequestTest, RequestForAnAddressTheNodeCannotDialIsRefused) {
    EXPECT_EQ(parse_refusal_of(R"({"address": "ftp://example"})"),
              "An address must start with http://, as http://192.168.1.10:8080 does: "
              "ftp://example");
}

TEST(DialRequestTest, RequestIsWrittenAsItIsDialed) {
    const DialRequest request("HTTP://192.168.1.10:8080/");

    EXPECT_EQ(request.to_string(), R"({"address":"http://192.168.1.10:8080"})");
    EXPECT_EQ(DialRequest::parse(request.to_string()).endpoint(), request.endpoint());
}
