#include <gtest/gtest.h>
#include <cstddef>
#include <limits>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "api/SearchHandler.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "msg/Channel.hpp"
#include "msg/Message.hpp"
#include "net/Address.hpp"
#include "stats/Recorder.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"
#include "storage/SearchAnswered.hpp"
#include "storage/SearchAnswers.hpp"
#include "storage/Zlib.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"
#include "types/Json.hpp"
#include "web/ConnectionInfo.hpp"

using api::SearchHandler;
using http::Request;
using http::Response;
using msg::Channel;
using net::Address;
using storage::HashPrefix;
using storage::Identifier;
using storage::SearchAnswered;
using storage::SearchAnswers;
using storage::Zlib;
using types::Converter;
using types::Json;
using web::ConnectionInfo;
using namespace std::chrono_literals;

using SearchRequested = stats::Recorder::SearchRequested;

// The tests only take SearchRequested and SearchAnswered, which the handler
// posts from another file, so nothing here would make their messages'
// virtual to_string(), which next<>() casts to.
template class msg::Message<SearchRequested>;
template class msg::Message<SearchAnswered>;

namespace {

// "hello"'s SHA-256 digest, as `shasum -a 256` gives it.
constexpr std::string_view hello_hash =
    "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";

// The id of a node, the identifier of its public key (HTTP API §11.2): here
// the SHA-256 of "abc" (FIPS 180-2 B.1).
constexpr std::string_view node_hash =
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

// Where a connection came from: an address set aside for documentation
// (RFC 5737), and a port the system might pick for a client.
constexpr std::string_view remote_host = "192.0.2.1";
constexpr Address::Port remote_port = 50'000;

// The address of this machine the connection reached: another set aside for
// documentation, and the port a node might listen on.
constexpr std::string_view local_host = "198.51.100.1";
constexpr Address::Port local_port = 8'080;

// The leading bits a match must share with what is searched for, as
// storage.search_min_bits is by default, and how long an answer is kept.
constexpr std::size_t min_bits = 24;
constexpr SearchAnswers::Milliseconds ttl = 300s;

// The SHA-256 identifier whose hash begins with start, the rest zeros.
Identifier content(std::string_view start) {
    constexpr std::size_t sha256_digits = 64;
    auto hash = std::string(start);

    hash.resize(sha256_digits, '0');
    return {"sha256", std::move(hash)};
}

Identifier hello_id() {
    return {"sha256", std::string(hello_hash)};
}

Identifier node_id() {
    return {"sha256", std::string(node_hash)};
}

// The answers to searches of a node that holds held.
std::shared_ptr<SearchAnswers> answers_of(std::vector<Identifier> held) {
    return std::make_shared<SearchAnswers>(
        [held = std::move(held)](const HashPrefix&) { return held; }, min_bits, ttl);
}

// A handler for a node that holds nothing.
SearchHandler empty_handler() {
    return SearchHandler(answers_of({}));
}

// A request of method for uri, with body.
Request request_of(std::string method, std::string uri, std::string body = "") {
    Request request;

    request.set_method(std::move(method));
    request.set_uri(std::move(uri));
    request.set_body(std::move(body));
    return request;
}

Request get(std::string uri) {
    return request_of("GET", std::move(uri));
}

// A GET that says the search may be passed on as relays says.
Request get(std::string uri, std::string relays) {
    auto request = get(std::move(uri));

    request.headers().set(std::string(types::search_relays_header), std::move(relays));
    return request;
}

Request post(std::string uri, std::string body) {
    return request_of("POST", std::move(uri), std::move(body));
}

// A connection that carries no node's signature.
ConnectionInfo anonymous() {
    return ConnectionInfo(Address(remote_host, remote_port), Address(local_host, local_port));
}

// A connection that carries the signature of the node node_id() names.
ConnectionInfo from_node() {
    auto connection = anonymous();

    connection.set_node(node_id());
    return connection;
}

// What handler answers request with, on connection.
Response answer(const SearchHandler& handler, const Request& request,
                const ConnectionInfo& connection) {
    Response response;

    handler(request, response, connection);
    return response;
}

// What handler answers request with, on a connection that carries no
// node's signature.
Response answer(const SearchHandler& handler, const Request& request) {
    return answer(handler, request, anonymous());
}

int status_of(const SearchHandler& handler, const Request& request) {
    return answer(handler, request).status_code();
}

// The type of the problem a response describes.
std::string problem_type(const Response& response) {
    return Json::parse(response.body()).at("type").string();
}

// The body of a search's answer naming identifiers.
std::string answer_naming(const std::vector<Identifier>& identifiers) {
    Json::Array results;

    for (const auto& identifier : identifiers) {
        results.emplace_back(identifier.to_string());
    }

    return Json(Json::Object{{"results", Json(std::move(results))}}).dump();
}

// How many times more a GET from a node, saying relays, may be passed on,
// as the handler posts it; nothing if it posts no search.
std::optional<std::size_t> relays_posted(const std::string& relays) {
    const auto handler = empty_handler();
    Channel requested(SearchRequested::name);

    (void)answer(handler, get("/data/search/2cf24d", relays), from_node());

    const auto posted = requested.next<SearchRequested>(0ms);

    if (!posted) {
        return std::nullopt;
    }

    return posted->relays;
}

} // namespace

// The tests read optionals after ASSERT_TRUE(x.has_value()), which this
// check does not follow.
// NOLINTBEGIN(bugprone-unchecked-optional-access)

// What shares the most leading bits comes first, and what shares fewer
// than min_bits is left out (HttpApi §6, §6.1).
TEST(SearchHandlerTest, GetAnswersBestMatchFirst) {
    const SearchHandler handler(answers_of({content("2cf24d"), hello_id(), content("2cf3")}));

    const auto response = answer(handler, get("/data/search/2cf24dba"));

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.headers().get("Content-Type"), "application/json");
    EXPECT_EQ(response.body(), answer_naming({hello_id(), content("2cf24d")}));
}

TEST(SearchHandlerTest, GetOfNothingNearIsAnEmptyAnswer) {
    const SearchHandler handler(answers_of({content("ffff")}));

    const auto response = answer(handler, get("/data/search/2cf24d"));

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.body(), R"({"results":[]})");
}

// A search no node asked, as a person's, names none, and says nothing of
// how far it may be passed on, whatever it says.
TEST(SearchHandlerTest, GetPostsTheSearch) {
    const auto handler = empty_handler();
    Channel requested(SearchRequested::name);

    (void)answer(handler, get("/data/search/2CF24D?from=test", "2"));
    const auto posted = requested.next<SearchRequested>(0ms);

    ASSERT_TRUE(posted.has_value());
    EXPECT_EQ(posted->prefix.to_string(), "2cf24d");
    EXPECT_FALSE(posted->node.has_value());
    EXPECT_EQ(posted->relays, 0U);
    EXPECT_TRUE(requested.empty());
}

TEST(SearchHandlerTest, GetNamesTheNodeThatAskedAndHowFarItMayGo) {
    const auto handler = empty_handler();
    Channel requested(SearchRequested::name);

    (void)answer(handler, get("/data/search/2cf24d", "2"), from_node());
    const auto posted = requested.next<SearchRequested>(0ms);

    ASSERT_TRUE(posted.has_value());
    ASSERT_TRUE(posted->node.has_value());
    EXPECT_EQ(posted->node->to_string(), node_id().to_string());
    EXPECT_EQ(posted->relays, 2U);
}

// A missing or unusable X-Search-Relays is none, and one larger than can
// be counted as many as can be, for the node to hold to what it allows
// (HttpApi §6).
TEST(SearchHandlerTest, UnusableRelaysAreNone) {
    const auto handler = empty_handler();
    Channel requested(SearchRequested::name);

    (void)answer(handler, get("/data/search/2cf24d"), from_node());
    const auto posted = requested.next<SearchRequested>(0ms);

    ASSERT_TRUE(posted.has_value());
    EXPECT_EQ(posted->relays, 0U);
    EXPECT_EQ(relays_posted("0"), 0U);
    EXPECT_EQ(relays_posted("17"), 17U);
    EXPECT_EQ(relays_posted(""), 0U);
    EXPECT_EQ(relays_posted("-1"), 0U);
    EXPECT_EQ(relays_posted("+1"), 0U);
    EXPECT_EQ(relays_posted("1.5"), 0U);
    EXPECT_EQ(relays_posted("two"), 0U);
    EXPECT_EQ(relays_posted("99999999999999999999999"), std::numeric_limits<std::size_t>::max());
    EXPECT_EQ(relays_posted("99999999999999999999999x"), 0U);
}

TEST(SearchHandlerTest, InvalidPrefixIsBadRequest) {
    const auto handler = empty_handler();
    Channel requested(SearchRequested::name);
    Channel answered(SearchAnswered::name);

    const auto response = answer(handler, get("/data/search/2cfx"));

    EXPECT_EQ(response.status_code(), 400);
    EXPECT_EQ(problem_type(response), "https://libranet.org/problems/invalid-search-prefix");
    EXPECT_EQ(status_of(handler, get("/data/search/" + std::string(hello_hash) + "0")), 400);
    EXPECT_EQ(answer(handler, post("/data/search/2cfx", answer_naming({hello_id()})), from_node())
                  .status_code(),
              400);
    EXPECT_TRUE(requested.empty());
    EXPECT_TRUE(answered.empty());
}

// The handler is registered for /data/search, so it sees whatever is
// beneath it.
TEST(SearchHandlerTest, OtherPathsAreNotFound) {
    const auto handler = empty_handler();

    const auto response = answer(handler, get("/data/search"));

    EXPECT_EQ(response.status_code(), 404);
    EXPECT_EQ(problem_type(response), "about:blank");
    EXPECT_EQ(status_of(handler, get("/data/search/")), 404);
    EXPECT_EQ(status_of(handler, get("/data/search/?from=test")), 404);
    EXPECT_EQ(status_of(handler, get("/data/search/2cf/24d")), 404);
    EXPECT_EQ(status_of(handler, get("/data/searching/2cf")), 404);
    EXPECT_EQ(status_of(handler, request_of("PUT", "/data/search")), 404);
}

TEST(SearchHandlerTest, OtherMethodsAreNotAllowed) {
    const auto handler = empty_handler();

    const auto response = answer(handler, request_of("PUT", "/data/search/2cf24d"));

    EXPECT_EQ(response.status_code(), 405);
    EXPECT_EQ(response.headers().get("Allow"), "GET, POST");
    EXPECT_EQ(response.headers().get("Content-Type"), "application/problem+json");
    EXPECT_EQ(status_of(handler, request_of("DELETE", "/data/search/2cf24d")), 405);
    EXPECT_EQ(status_of(handler, request_of("HEAD", "/data/search/2cf24d")), 405);
    EXPECT_EQ(status_of(handler, request_of("PUT", "/data/search/2cfx")), 405);
}

// What a peer names is posted as it named it, for what takes it to keep
// what matches (HttpApi §10.7.2).
TEST(SearchHandlerTest, PostIsAcceptedAndWhatItNamedIsPosted) {
    const auto handler = empty_handler();
    Channel answered(SearchAnswered::name);
    Channel requested(SearchRequested::name);
    const auto named = answer_naming({hello_id(), content("ffff")});

    const auto response = answer(handler, post("/data/search/2CF24D", named), from_node());
    const auto posted = answered.next<SearchAnswered>(0ms);

    EXPECT_EQ(response.status_code(), 202);
    EXPECT_EQ(response.body(), "");
    ASSERT_TRUE(posted.has_value());
    EXPECT_EQ(posted->prefix.to_string(), "2cf24d");
    EXPECT_EQ(posted->node.to_string(), node_id().to_string());
    ASSERT_EQ(posted->results.size(), 2U);
    EXPECT_EQ(posted->results.at(0).to_string(), hello_id().to_string());
    EXPECT_EQ(posted->results.at(1).to_string(), content("ffff").to_string());
    EXPECT_TRUE(answered.empty());
    EXPECT_TRUE(requested.empty());
}

TEST(SearchHandlerTest, PostSentCompressedIsRead) {
    const auto handler = empty_handler();
    Channel answered(SearchAnswered::name);
    const auto compressed = Converter::text_of(
        Zlib::compress(Converter::bytes_of(answer_naming({hello_id()})), Zlib::max_level));

    const auto response = answer(handler, post("/data/search/2cf24d", compressed), from_node());
    const auto posted = answered.next<SearchAnswered>(0ms);

    EXPECT_EQ(response.status_code(), 202);
    ASSERT_TRUE(posted.has_value());
    ASSERT_EQ(posted->results.size(), 1U);
    EXPECT_EQ(posted->results.at(0).to_string(), hello_id().to_string());
}

TEST(SearchHandlerTest, PostNamingNothingPostsNothing) {
    const auto handler = empty_handler();
    Channel answered(SearchAnswered::name);

    const auto response =
        answer(handler, post("/data/search/2cf24d", R"({"results":["md5/2cf2"]})"), from_node());

    EXPECT_EQ(response.status_code(), 202);
    EXPECT_TRUE(answered.empty());
}

// What is named must be from a node, whose signature the connection carries
// once it is verified (HttpApi §10.7.2).
TEST(SearchHandlerTest, PostFromNoNodeIsUnauthorized) {
    const auto handler = empty_handler();
    Channel answered(SearchAnswered::name);

    const auto response = answer(handler, post("/data/search/2cf24d", answer_naming({hello_id()})));

    EXPECT_EQ(response.status_code(), 401);
    EXPECT_EQ(problem_type(response), "https://libranet.org/problems/signature-required");
    EXPECT_TRUE(answered.empty());
}

// A body that is not JSON, not an object holding a "results" array, or that
// decompresses past the handler's limit, is no search's answer.
TEST(SearchHandlerTest, PostThatIsNoAnswerIsBadRequest) {
    const auto handler = empty_handler();
    const auto named = answer_naming({hello_id()});
    const SearchHandler cramped(answers_of({}), named.size() - 1);
    const auto compressed =
        Converter::text_of(Zlib::compress(Converter::bytes_of(named), Zlib::max_level));
    Channel answered(SearchAnswered::name);

    const auto response = answer(handler, post("/data/search/2cf24d", "not JSON"), from_node());

    EXPECT_EQ(response.status_code(), 400);
    EXPECT_EQ(problem_type(response), "https://libranet.org/problems/invalid-list");
    EXPECT_EQ(answer(handler, post("/data/search/2cf24d", ""), from_node()).status_code(), 400);
    EXPECT_EQ(
        answer(handler, post("/data/search/2cf24d", R"({"result":[]})"), from_node()).status_code(),
        400);
    EXPECT_EQ(answer(cramped, post("/data/search/2cf24d", compressed), from_node()).status_code(),
              400);
    EXPECT_EQ(answer(cramped, post("/data/search/2cf24d", named), from_node()).status_code(), 202);
    (void)answered.next<SearchAnswered>(0ms);
    EXPECT_TRUE(answered.empty());
}

TEST(SearchHandlerTest, NoAnswersIsRefused) {
    EXPECT_THROW((void)SearchHandler(nullptr), std::invalid_argument);
}

// NOLINTEND(bugprone-unchecked-optional-access)
