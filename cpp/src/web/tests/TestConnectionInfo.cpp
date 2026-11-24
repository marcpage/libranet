#include <gtest/gtest.h>
#include <string>
#include <string_view>

#include "net/Address.hpp"
#include "storage/Identifier.hpp"
#include "web/ConnectionInfo.hpp"

using net::Address;
using storage::Identifier;
using web::ConnectionInfo;

namespace {

// Where a connection came from: an address set aside for documentation
// (RFC 5737), and a port the system might pick for a client.
constexpr std::string_view remote_host = "192.0.2.1";
constexpr Address::Port remote_port = 50000;

// The ids of two nodes, each the identifier of its public key (HTTP API
// §11.2): here the SHA-256 of nothing, and of "abc" (FIPS 180-2 B.1).
constexpr std::string_view first_node =
    "sha256/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
constexpr std::string_view second_node =
    "sha256/ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

// A connection from remote_host and remote_port.
ConnectionInfo from_remote() {
    return ConnectionInfo(Address(remote_host, remote_port));
}

// The id of the node the connection knows, or "" when it knows none.
std::string node_of(const ConnectionInfo& connection) {
    const auto& node = connection.node();

    return node.has_value() ? node->to_string() : "";
}

} // namespace

TEST(ConnectionInfoTest, KeepsTheAddressItCameFrom) {
    const auto connection = from_remote();

    EXPECT_EQ(connection.remote_address().host(), remote_host);
    EXPECT_EQ(connection.remote_address().port(), remote_port);
}

TEST(ConnectionInfoTest, CountsEachRequest) {
    auto connection = from_remote();

    EXPECT_EQ(connection.requests(), 0U);

    connection.add_a_request();
    EXPECT_EQ(connection.requests(), 1U);

    connection.add_a_request();
    connection.add_a_request();
    EXPECT_EQ(connection.requests(), 3U);
}

TEST(ConnectionInfoTest, KnowsNoNodeUntilOneIsSet) {
    const auto connection = from_remote();

    EXPECT_FALSE(connection.node().has_value());
}

// The node set last is the one known, as a later request may be signed by
// another.
TEST(ConnectionInfoTest, KnowsTheNodeSetLast) {
    auto connection = from_remote();

    connection.set_node(Identifier::parse(first_node));
    EXPECT_EQ(node_of(connection), first_node);

    connection.set_node(Identifier::parse(second_node));
    EXPECT_EQ(node_of(connection), second_node);
}

TEST(ConnectionInfoTest, StaysOpenUntilClosedAfterTheAnswer) {
    auto connection = from_remote();

    EXPECT_FALSE(connection.is_closing());

    connection.close_after_answer();
    EXPECT_TRUE(connection.is_closing());
}
