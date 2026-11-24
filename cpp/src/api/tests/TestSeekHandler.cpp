#include <gtest/gtest.h>
#include <cstddef>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include "api/SeekHandler.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "logger/ILogOutput.hpp"
#include "logger/Logger.hpp"
#include "net/Address.hpp"
#include "protocol/SeekList.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"
#include "storage/Zlib.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"
#include "types/Json.hpp"
#include "web/ConnectionInfo.hpp"

using api::SeekHandler;
using http::Request;
using http::Response;
using logger::ILogOutput;
using logger::Logger;
using net::Address;
using protocol::SeekList;
using storage::HashPrefix;
using storage::Identifier;
using storage::Zlib;
using types::Converter;
using types::Json;
using web::ConnectionInfo;

namespace {

// The SHA-256 digest of "hello", as `shasum -a 256` gives it.
constexpr std::string_view hello_hash =
    "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";

// The id of a node, the identifier of its public key (HTTP API §11.2): here
// the SHA-256 of "abc" (FIPS 180-2 B.1).
constexpr std::string_view node_hash =
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

// Where a connection came from: an address set aside for documentation
// (RFC 5737), and a port the system might pick for a client.
constexpr std::string_view remote_host = "192.0.2.1";
constexpr Address::Port remote_port = 50000;

// A list that seeks hello, by identifier and by the prefix of its hash, as
// it is posted.
constexpr std::string_view hello_list_text =
    R"({"data": ["sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"],)"
    R"( "search": ["2cf2"]})";

// A list that seeks hello, by identifier and by the prefix of its hash.
SeekList hello_list() {
    return {{Identifier("sha256", std::string(hello_hash))}, {HashPrefix::parse("2cf2")}};
}

// A request of method for uri, with body.
Request request_of(std::string method, std::string uri, std::string body = "") {
    Request request;

    request.set_method(std::move(method));
    request.set_uri(std::move(uri));
    request.set_body(std::move(body));
    return request;
}

Request get(std::string uri = "/data/seek") {
    return request_of("GET", std::move(uri));
}

Request post(std::string body) {
    return request_of("POST", "/data/seek", std::move(body));
}

// A connection that carries no node's signature.
ConnectionInfo unsigned_connection() {
    return ConnectionInfo(Address(remote_host, remote_port));
}

// A connection that carries the signature of the node node_hash names.
ConnectionInfo signed_connection() {
    auto connection = unsigned_connection();

    connection.set_node(Identifier("sha256", std::string(node_hash)));
    return connection;
}

// What handler answers request with, on connection.
Response answer(const SeekHandler& handler, const Request& request,
                const ConnectionInfo& connection) {
    Response response;

    handler(request, response, connection);
    return response;
}

// What handler answers request with, on a connection that carries no
// node's signature.
Response answer(const SeekHandler& handler, const Request& request) {
    return answer(handler, request, unsigned_connection());
}

// The status handler answers request with.
int status_of(const SeekHandler& handler, const Request& request) {
    return answer(handler, request).status_code();
}

// The body handler answers a GET of /data/seek with.
std::string served(const SeekHandler& handler) {
    return std::string(answer(handler, get()).body());
}

// The type of the problem a response describes.
std::string problem_type(const Response& response) {
    return Json::parse(response.body()).at("type").string();
}

// Keeps each line it is given, where a test can read them while the Logger
// owns it.
class KeepingOutput : public ILogOutput {
public:
    explicit KeepingOutput(std::shared_ptr<std::vector<std::string>> lines)
        : m_lines(std::move(lines)) {
    }

    void log(std::string_view line) override {
        m_lines->emplace_back(line);
    }

private:
    std::shared_ptr<std::vector<std::string>> m_lines;
};

// Posts hello's list on a signed connection, writes what was logged at Info
// and above to stderr, and ends the program, with as many lines as were
// logged as its status.
[[noreturn]] void post_and_exit() {
    const SeekHandler handler;
    const auto lines = std::make_shared<std::vector<std::string>>();

    Logger::remove_all_outputs();
    Logger::add_output(std::make_unique<KeepingOutput>(lines));
    Logger::reset_levels(Logger::Level::Info);
    (void)answer(handler, post(std::string(hello_list_text)), signed_connection());
    Logger::remove_all_outputs();

    for (const auto& line : *lines) {
        std::cerr << line << '\n';
    }

    std::_Exit(static_cast<int>(lines->size()));
}

} // namespace

// Until the node has derived its seek list, it seeks nothing.
TEST(SeekHandlerTest, EmptyListIsServedUntilOneIsSet) {
    const SeekHandler handler;

    const auto response = answer(handler, get());

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.headers().get("Content-Type"), "application/json");
    EXPECT_EQ(response.body(), R"({"data":[],"search":[]})");
}

TEST(SeekHandlerTest, ListSetIsServed) {
    SeekHandler handler;

    handler.set_list(hello_list());

    EXPECT_EQ(served(handler), R"({"data":["sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa)"
                               R"(7425e73043362938b9824"],"search":["2cf2"]})");
    handler.set_list(SeekList());
    EXPECT_EQ(served(handler), R"({"data":[],"search":[]})");
}

// The list served is sent in fewer bytes than the handler is told.
TEST(SeekHandlerTest, ListSetIsServedWithinTheBytesItIsGiven) {
    constexpr std::size_t served_bytes = 32;
    SeekHandler cramped(types::max_decompressed_list_size, served_bytes);
    SeekHandler roomy;

    cramped.set_list(hello_list());
    roomy.set_list(hello_list());

    EXPECT_LT(served(cramped).size(), served_bytes);
    EXPECT_GE(served(roomy).size(), served_bytes);
}

// A query does not change what the path names.
TEST(SeekHandlerTest, QueryIsTaken) {
    const SeekHandler handler;

    EXPECT_EQ(status_of(handler, get("/data/seek?from=test")), 200);
}

// A list a peer posts is taken, and not served as the node's own.
TEST(SeekHandlerTest, PostedListIsAccepted) {
    const SeekHandler handler;

    const auto from_node = answer(handler, post(std::string(hello_list_text)), signed_connection());
    const auto unsigned_post = answer(handler, post(std::string(hello_list_text)));

    EXPECT_EQ(from_node.status_code(), 202);
    EXPECT_TRUE(from_node.body().empty());
    EXPECT_EQ(unsigned_post.status_code(), 202);
    EXPECT_EQ(served(handler), R"({"data":[],"search":[]})");
}

// A list may be sent zlib-compressed (HttpApi §10.7.1).
TEST(SeekHandlerTest, CompressedPostedListIsAccepted) {
    const SeekHandler handler;
    const auto compressed =
        Converter::text_of(Zlib::compress(Converter::bytes_of(hello_list_text), Zlib::max_level));

    EXPECT_EQ(status_of(handler, post(compressed)), 202);
}

TEST(SeekHandlerDeathTest, PostedListIsLogged) {
    GTEST_FLAG_SET(death_test_style, "threadsafe");

    EXPECT_EXIT(post_and_exit(), testing::ExitedWithCode(1),
                "sha256/" + std::string(node_hash) + " seeks 1 identifiers and 1 prefixes");
}

// What is not a seek list is refused as one (HttpApi §10.5).
TEST(SeekHandlerTest, PostThatIsNotAListIsBadRequest) {
    const SeekHandler handler;

    const auto response = answer(handler, post("not a list"));

    EXPECT_EQ(response.status_code(), 400);
    EXPECT_EQ(response.headers().get("Content-Type"), "application/problem+json");
    EXPECT_EQ(problem_type(response), "https://libranet.org/problems/invalid-list");
    EXPECT_EQ(status_of(handler, post("")), 400);
    EXPECT_EQ(status_of(handler, post("[]")), 400);
    EXPECT_EQ(status_of(handler, post(R"({"data": "sha256/2cf2"})")), 400);
}

// A compressed list that holds more than the handler takes once
// decompressed is refused, as the protocol sets no limit of its own. One
// sent as it is is held to none.
TEST(SeekHandlerTest, ListTooLargeOnceDecompressedIsBadRequest) {
    constexpr std::size_t list_bytes = 16;
    const SeekHandler handler(list_bytes);
    const auto compressed =
        Converter::text_of(Zlib::compress(Converter::bytes_of(hello_list_text), Zlib::max_level));

    const auto response = answer(handler, post(compressed));

    EXPECT_EQ(response.status_code(), 400);
    EXPECT_EQ(problem_type(response), "https://libranet.org/problems/invalid-list");
    EXPECT_EQ(status_of(handler, post(std::string(hello_list_text))), 202);
}

// A method /data/seek does not take is 405, naming those it does
// (HttpApi §4).
TEST(SeekHandlerTest, OtherMethodsAreNotAllowed) {
    const SeekHandler handler;

    const auto response = answer(handler, request_of("PUT", "/data/seek", "{}"));

    EXPECT_EQ(response.status_code(), 405);
    EXPECT_EQ(response.headers().get("Allow"), "GET, POST");
    EXPECT_EQ(status_of(handler, request_of("DELETE", "/data/seek")), 405);
}

TEST(SeekHandlerTest, OtherPathsAreNotFound) {
    const SeekHandler handler;

    const auto response = answer(handler, get("/data/seek/2cf2"));

    EXPECT_EQ(response.status_code(), 404);
    EXPECT_EQ(response.headers().get("Content-Type"), "application/problem+json");
    EXPECT_EQ(status_of(handler, get("/data/seeks")), 404);
    EXPECT_EQ(status_of(handler, get("/data")), 404);
}

// A list set while GETs are answered is served whole, or the one before it.
TEST(SeekHandlerTest, ListIsSetWhileServed) {
    constexpr auto rounds = 100;
    SeekHandler handler;
    const auto empty = served(handler);
    const auto hello = Converter::text_of(hello_list().body());
    std::thread setter([&] {
        for (auto round = 0; round < rounds; ++round) {
            handler.set_list(hello_list());
        }
    });

    for (auto round = 0; round < rounds; ++round) {
        const auto body = served(handler);

        EXPECT_TRUE(empty == body || hello == body);
    }

    setter.join();
}
