#include <gtest/gtest.h>
#include <algorithm>
#include <cstddef>
#include <iterator>
#include <map>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "api/NodesHandler.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "msg/Channel.hpp"
#include "msg/Message.hpp"
#include "net/Address.hpp"
#include "protocol/NodeList.hpp"
#include "stats/NodeAddresses.hpp"
#include "stats/Recorder.hpp"
#include "storage/Identifier.hpp"
#include "storage/Zlib.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"
#include "types/Json.hpp"
#include "web/ConnectionInfo.hpp"

using api::NodesHandler;
using http::Request;
using http::Response;
using msg::Channel;
using net::Address;
using protocol::NodeList;
using stats::NodeAddresses;
using stats::Recorder;
using storage::Identifier;
using storage::Zlib;
using types::Converter;
using types::Json;
using web::ConnectionInfo;
using namespace std::chrono_literals;

// The tests only take the lists the handler posts, from another file, so
// nothing here would make their messages' virtual to_string(), which
// next<>() casts to.
template class msg::Message<Recorder::NodesReceived>;

namespace {

using NodesReceived = Recorder::NodesReceived;
using Source = NodeAddresses::Source;
using Sources = std::map<std::string, Source>;
// An entry of a node list, as endpoint and node id.
using Named = std::pair<std::string, std::string>;

// Where a connection came from: an address set aside for documentation
// (RFC 5737), and a port the system might pick for a client.
constexpr std::string_view remote_host = "203.0.113.42";
constexpr Address::Port remote_port = 50000;

// This node's own endpoint, as it names itself when it does not know the
// address it is reached at (HttpApi §10.1).
constexpr std::string_view own_endpoint = "http://localhost:4300";

// The id of a node whose hash is digit, over and over.
Identifier node(char digit) {
    return {"sha256", std::string(Identifier::longest_hash_digits(), digit)};
}

// This node's own id, and the id of the peer that sends it a list.
Identifier own_id() {
    return node('0');
}

Identifier peer_id() {
    return node('1');
}

// A list naming each node of digit at the endpoint beside it.
NodeList list_of(const std::vector<std::pair<std::string_view, char>>& entries) {
    NodeList list;

    for (const auto& [endpoint, digit] : entries) {
        list.add(std::string(endpoint), node(digit));
    }

    return list;
}

// The entries of list, in order.
std::vector<Named> named_in(const NodeList& list) {
    std::vector<Named> named;

    std::transform(list.entries().begin(), list.entries().end(), std::back_inserter(named),
                   [](const NodeList::Entry& entry) {
        return Named(entry.endpoint, entry.node_id.to_string());
    });
    return named;
}

// The entry naming the node of digit at endpoint.
Named named(std::string_view endpoint, char digit) {
    return {std::string(endpoint), node(digit).to_string()};
}

// A handler serving this node's own endpoint.
NodesHandler own_handler() {
    return {own_id(), {std::string(own_endpoint)}};
}

// A request of method for uri, with body.
Request request_of(std::string method, std::string uri, std::string body = "") {
    Request request;

    request.set_method(std::move(method));
    request.set_uri(std::move(uri));
    request.set_body(std::move(body));
    return request;
}

Request get(std::string uri = std::string(types::nodes_path)) {
    return request_of("GET", std::move(uri));
}

Request post(std::string body) {
    return request_of("POST", std::string(types::nodes_path), std::move(body));
}

// A connection from host, carrying the signature of the peer.
ConnectionInfo peer_connection(std::string_view host = remote_host) {
    ConnectionInfo connection(Address(host, remote_port));

    connection.set_node(peer_id());
    return connection;
}

// What handler answers request with, on connection.
Response answer(const NodesHandler& handler, const Request& request,
                const ConnectionInfo& connection) {
    Response response;

    handler(request, response, connection);
    return response;
}

// What handler answers request with, on a connection from the peer.
Response answer(const NodesHandler& handler, const Request& request) {
    return answer(handler, request, peer_connection());
}

// The type of the problem a response describes.
std::string problem_type(const Response& response) {
    return Json::parse(response.body()).at("type").string();
}

} // namespace

// The tests read optionals after ASSERT_TRUE(x.has_value()), which this
// check does not follow.
// NOLINTBEGIN(bugprone-unchecked-optional-access)

// Until it is given another list, it serves the node's own endpoints alone.
TEST(NodesHandlerTest, ServesItsOwnEndpointsAtFirst) {
    const auto handler = own_handler();

    const auto response = answer(handler, get());

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.headers().get("Content-Type"), "application/json");
    EXPECT_EQ(response.body(), R"({"nodes":{"http://localhost:4300":")" + own_id().to_string()
                                   + R"("}})");
}

TEST(NodesHandlerTest, ServesTheListItIsGiven) {
    auto handler = own_handler();
    const auto nodes = list_of({{own_endpoint, '0'}, {"http://192.0.2.5:4300", '2'}});

    handler.set_nodes(nodes);

    const auto response = answer(handler, get());

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.body(), nodes.to_string());
}

// A list whose JSON is too large to send is served compressed, within
// 1 MiB (HttpApi §10.6).
TEST(NodesHandlerTest, ServesALargeListCompressed) {
    constexpr std::size_t count = 10'000;
    auto handler = own_handler();
    NodeList nodes;

    for (std::size_t index = 0; index < count; ++index) {
        nodes.add("http://node-" + std::to_string(index) + ".example:4300", node('2'));
    }

    handler.set_nodes(nodes);

    const auto response = answer(handler, get());
    const auto served = NodeList::from_body(Converter::bytes_of(response.body()));

    ASSERT_GE(nodes.to_string().size(), NodeList::max_size);
    EXPECT_EQ(response.status_code(), 200);
    EXPECT_LT(response.body().size(), NodeList::max_size);
    EXPECT_EQ(served.entries().size(), count);
}

// The list served is sent in fewer bytes than the handler is told, leaving
// out the entries at its end that do not fit.
TEST(NodesHandlerTest, ServesTheListWithinTheBytesItIsGiven) {
    constexpr std::size_t served_bytes = 64;
    NodesHandler handler(own_id(), {std::string(own_endpoint)}, types::max_decompressed_list_size,
                         served_bytes);
    const auto nodes = list_of({{own_endpoint, '0'}, {"http://192.0.2.5:4300", '2'}});

    handler.set_nodes(nodes);

    const auto response = answer(handler, get());
    const auto served = NodeList::from_body(Converter::bytes_of(response.body()));

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_LT(response.body().size(), served_bytes);
    EXPECT_LT(served.entries().size(), nodes.entries().size());
}

// A query does not change what the path names, and a path beneath it names
// nothing.
TEST(NodesHandlerTest, QueryIsIgnoredAndPathBeneathIsNotFound) {
    const auto handler = own_handler();

    const auto beneath = answer(handler, get("/data/nodes/more"));

    EXPECT_EQ(answer(handler, get("/data/nodes?from=test")).status_code(), 200);
    EXPECT_EQ(beneath.status_code(), 404);
    EXPECT_EQ(problem_type(beneath), "about:blank");
}

TEST(NodesHandlerTest, OtherMethodsAreNotAllowed) {
    const auto handler = own_handler();
    Channel received(NodesReceived::name);

    const auto response = answer(handler, request_of("PUT", "/data/nodes", "{\"nodes\":{}}"));

    EXPECT_EQ(response.status_code(), 405);
    EXPECT_EQ(response.headers().get("Allow"), "GET, POST");
    EXPECT_EQ(answer(handler, request_of("DELETE", "/data/nodes")).status_code(), 405);
    EXPECT_TRUE(received.empty());
}

// A list posted is answered 202 and posted, with localhost resolved to the
// address the connection came from (HttpApi §10.2). The peer's own entries
// were observed where they were resolved and advertised where they were
// not; the rest it relayed.
TEST(NodesHandlerTest, PostedListIsResolvedAndPosted) {
    const auto handler = own_handler();
    Channel received(NodesReceived::name);
    const auto sent = list_of({
        {"http://localhost:4300", '1'},
        {"http://198.51.100.7:4300", '1'},
        {"http://192.0.2.5:4300", '2'},
    });

    const auto response = answer(handler, post(sent.to_string()));
    const auto posted = received.next<NodesReceived>(0ms);

    EXPECT_EQ(response.status_code(), 202);
    EXPECT_TRUE(response.body().empty());
    ASSERT_TRUE(posted.has_value());
    EXPECT_EQ(named_in(posted->nodes), (std::vector<Named>{
                                           named("http://192.0.2.5:4300", '2'),
                                           named("http://198.51.100.7:4300", '1'),
                                           named("http://203.0.113.42:4300", '1'),
                                       }));
    EXPECT_EQ(posted->sources, (Sources{
                                   {"http://198.51.100.7:4300", Source::Advertised},
                                   {"http://203.0.113.42:4300", Source::Observed},
                               }));
    EXPECT_TRUE(received.empty());
}

// A list may be sent zlib-compressed (HttpApi §10.6).
TEST(NodesHandlerTest, CompressedListIsRead) {
    const auto handler = own_handler();
    Channel received(NodesReceived::name);
    const auto sent = list_of({{"http://192.0.2.5:4300", '2'}});
    const auto compressed = Zlib::compress(Converter::bytes_of(sent.to_string()), Zlib::max_level);

    const auto response = answer(handler, post(Converter::text_of(compressed)));
    const auto posted = received.next<NodesReceived>(0ms);

    EXPECT_EQ(response.status_code(), 202);
    ASSERT_TRUE(posted.has_value());
    EXPECT_EQ(named_in(posted->nodes),
              (std::vector<Named>{named("http://192.0.2.5:4300", '2')}));
}

// A compressed list that holds more than the handler takes once
// decompressed is refused, and not posted. One sent as it is is held to
// none.
TEST(NodesHandlerTest, ListTooLargeOnceDecompressedIsInvalid) {
    constexpr std::size_t list_bytes = 16;
    const NodesHandler handler(own_id(), {std::string(own_endpoint)}, list_bytes);
    Channel received(NodesReceived::name);
    const auto sent = list_of({{"http://192.0.2.5:4300", '2'}});
    const auto compressed = Zlib::compress(Converter::bytes_of(sent.to_string()), Zlib::max_level);

    const auto response = answer(handler, post(Converter::text_of(compressed)));

    EXPECT_EQ(response.status_code(), 400);
    EXPECT_EQ(problem_type(response), "https://libranet.org/problems/invalid-list");
    EXPECT_TRUE(received.empty());
    EXPECT_EQ(answer(handler, post(sent.to_string())).status_code(), 202);
}

// A list from this machine keeps its loopback endpoints as sent, since the
// sender's loopback address is this node's too (HttpApi §10.2).
TEST(NodesHandlerTest, LoopbackEndpointFromThisMachineIsKeptAsSent) {
    const auto handler = own_handler();
    Channel received(NodesReceived::name);
    const auto sent = list_of({{"http://127.0.0.1:4301", '1'}});

    (void)answer(handler, post(sent.to_string()), peer_connection("127.0.0.1"));

    const auto posted = received.next<NodesReceived>(0ms);

    ASSERT_TRUE(posted.has_value());
    EXPECT_EQ(named_in(posted->nodes),
              (std::vector<Named>{named("http://127.0.0.1:4301", '1')}));
    EXPECT_EQ(posted->sources, (Sources{{"http://127.0.0.1:4301", Source::Advertised}}));
}

// With no signature on the connection, no entry is the sender's own.
TEST(NodesHandlerTest, ListFromNoNodeWasRelayed) {
    const auto handler = own_handler();
    Channel received(NodesReceived::name);
    const auto sent = list_of({{"http://localhost:4300", '1'}});

    const auto response = answer(handler, post(sent.to_string()),
                                 ConnectionInfo(Address(remote_host, remote_port)));
    const auto posted = received.next<NodesReceived>(0ms);

    EXPECT_EQ(response.status_code(), 202);
    ASSERT_TRUE(posted.has_value());
    EXPECT_EQ(named_in(posted->nodes),
              (std::vector<Named>{named("http://203.0.113.42:4300", '1')}));
    EXPECT_TRUE(posted->sources.empty());
}

// What is not JSON, nor JSON once decompressed, nor of the schema, is not a
// list (HttpApi §10.5), and is not posted.
TEST(NodesHandlerTest, BodyThatIsNoListIsInvalid) {
    const auto handler = own_handler();
    Channel received(NodesReceived::name);
    const auto compressed_array =
        Converter::text_of(Zlib::compress(Converter::bytes_of("[]"), Zlib::max_level));

    const auto response = answer(handler, post("not a list"));

    EXPECT_EQ(response.status_code(), 400);
    EXPECT_EQ(response.headers().get("Content-Type"), "application/problem+json");
    EXPECT_EQ(problem_type(response), "https://libranet.org/problems/invalid-list");
    EXPECT_EQ(answer(handler, post("")).status_code(), 400);
    EXPECT_EQ(answer(handler, post("[]")).status_code(), 400);
    EXPECT_EQ(answer(handler, post(R"({"peers":{}})")).status_code(), 400);
    EXPECT_EQ(answer(handler, post(R"({"nodes":[]})")).status_code(), 400);
    EXPECT_EQ(answer(handler, post(compressed_array)).status_code(), 400);
    EXPECT_TRUE(received.empty());
}

TEST(NodesHandlerTest, RefusesAnOwnEndpointThatIsNone) {
    EXPECT_THROW((void)NodesHandler(own_id(), {"localhost:4300"}), std::invalid_argument);
    EXPECT_THROW((void)NodesHandler(own_id(), {"http://localhost:4300/data"}),
                 std::invalid_argument);
}

// NOLINTEND(bugprone-unchecked-optional-access)
