#include <gtest/gtest.h>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "config/NodeHandler.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "net/Address.hpp"
#include "storage/Identifier.hpp"
#include "types/Json.hpp"
#include "web/ConnectionInfo.hpp"

using config::NodeHandler;
using http::Request;
using http::Response;
using net::Address;
using std::string;
using std::string_view;
using std::vector;
using storage::Identifier;
using types::Json;
using web::ConnectionInfo;

namespace {

// Where the main port listens: every interface, at the port a node listens
// on by default.
constexpr string_view main_host = "::";
constexpr Address::Port main_port = 8'080;

// The endpoint the node names itself at when it does not know the address it
// is reached at (HTTP API §10.1), and one it might be reached at from
// elsewhere.
constexpr string_view direct_endpoint = "http://localhost:8080";
constexpr string_view external_endpoint = "https://node.example:443";

// The node's id: its hash is a digit, over and over.
Identifier own_id() {
    return {"sha256", string(Identifier::longest_hash_digits(), '7')};
}

Address main_address() {
    return {main_host, main_port};
}

// A connection from this machine, from a port the system might pick for a
// client, to the port /config might take.
constexpr string_view loopback = "127.0.0.1";
constexpr Address::Port client_port = 50'000;
constexpr Address::Port config_port = 8'180;

ConnectionInfo connection() {
    return {Address(loopback, client_port), Address(loopback, config_port)};
}

Request request_of(string method, string uri = string(NodeHandler::path)) {
    Request request;

    request.set_method(std::move(method));
    request.set_uri(std::move(uri));
    return request;
}

// What handler answers request with.
Response answer(const NodeHandler& handler, const Request& request) {
    Response response;

    handler(request, response, connection());
    return response;
}

// The node handler names itself at direct_endpoint alone.
NodeHandler direct_handler() {
    return {own_id(), main_address(), {string(direct_endpoint)}};
}

// What handler says the node is.
Json description(const NodeHandler& handler) {
    return Json::parse(answer(handler, request_of("GET")).body());
}

} // namespace

TEST(ConfigNodeHandlerTest, AnswersAGetWithJson) {
    const auto response = answer(direct_handler(), request_of("GET"));

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.headers().get("Content-Type", ""), "application/json");
}

TEST(ConfigNodeHandlerTest, SaysWhatTheNodeIs) {
    const auto node = description(direct_handler());

    EXPECT_EQ(node.at("node_id").string(), own_id().to_string());
    EXPECT_EQ(node.at("listen_address").string(), main_host);
    EXPECT_EQ(node.at("listen_port").integer(), main_port);
    EXPECT_EQ(node.at("advertised_endpoint").string(), direct_endpoint);
}

// The Python node says nothing else of itself until people come.
TEST(ConfigNodeHandlerTest, SaysNothingElse) {
    EXPECT_EQ(description(direct_handler()).object().size(), 4U);
}

TEST(ConfigNodeHandlerTest, NamesAnIPv4ListenAddressAsItIs) {
    const NodeHandler handler(own_id(), Address("0.0.0.0", main_port), {string(direct_endpoint)});

    EXPECT_EQ(description(handler).at("listen_address").string(), "0.0.0.0");
}

// The first endpoint the node list names the node at is the one it
// advertises.
TEST(ConfigNodeHandlerTest, AdvertisesTheFirstOwnEndpoint) {
    const NodeHandler handler(own_id(), main_address(),
                              {string(external_endpoint), string(direct_endpoint)});

    EXPECT_EQ(description(handler).at("advertised_endpoint").string(), external_endpoint);
}

TEST(ConfigNodeHandlerTest, AnswersWhateverTheQuery) {
    const auto response = answer(direct_handler(),
                                 request_of("GET", string(NodeHandler::path) + "?x=1"));

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(Json::parse(response.body()).at("advertised_endpoint").string(), direct_endpoint);
}

TEST(ConfigNodeHandlerTest, RefusesAnyOtherMethod) {
    for (const auto* const method : {"POST", "PUT", "DELETE", "PATCH", "HEAD"}) {
        const auto response = answer(direct_handler(), request_of(method));

        EXPECT_EQ(response.status_code(), 405) << method;
        EXPECT_EQ(response.headers().get("Allow", ""), "GET") << method;
    }
}

TEST(ConfigNodeHandlerTest, ServesNothingBeneathIt) {
    for (const auto* const uri : {"/config/api/node/", "/config/api/node/x"}) {
        const auto response = answer(direct_handler(), request_of("GET", uri));

        EXPECT_EQ(response.status_code(), 404) << uri;
        EXPECT_EQ(Json::parse(response.body()).at("type").string(), "about:blank") << uri;
    }
}

TEST(ConfigNodeHandlerTest, RefusesANodeNamedAtNoEndpoint) {
    EXPECT_THROW(const NodeHandler refused(own_id(), main_address(), vector<string>{}),
                 std::invalid_argument);
}
