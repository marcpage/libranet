#include <gtest/gtest.h>
#include <chrono>
#include <cstdint>
#include <map>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "api/DataHandler.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "msg/Channel.hpp"
#include "msg/Message.hpp"
#include "net/Address.hpp"
#include "storage/DataNotFound.hpp"
#include "storage/DataRequested.hpp"
#include "storage/DataStored.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "storage/Zlib.hpp"
#include "types/Converter.hpp"
#include "types/Json.hpp"
#include "web/ConnectionInfo.hpp"

using api::DataHandler;
using http::Request;
using http::Response;
using msg::Channel;
using net::Address;
using storage::DataNotFound;
using storage::DataRequested;
using storage::DataStored;
using storage::HashPrefix;
using storage::IDataSource;
using storage::Identifier;
using storage::Zlib;
using types::Converter;
using types::Json;
using web::ConnectionInfo;
using namespace std::chrono_literals;

// The tests only take DataStored, DataNotFound, and DataRequested, which the
// handler posts from another file, so nothing here would make their
// messages' virtual to_string(), which next<>() casts to.
template class msg::Message<DataStored>;
template class msg::Message<DataNotFound>;
template class msg::Message<DataRequested>;

namespace {

// "hello", and its SHA-256 digest, as `shasum -a 256` gives it.
constexpr std::string_view hello = "hello";
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

// A store that keeps what it is given in memory, by identifier. put() is
// const in IDataSource, so what it keeps is mutable.
class MemoryStore : public IDataSource {
public:
    [[nodiscard]] bool has(const Identifier& identifier) const override;
    [[nodiscard]] std::optional<Data> get(const Identifier& identifier) const override;
    void put(const Identifier& identifier, const Data& body) const override;
    [[nodiscard]] bool remove(const Identifier& identifier) const override;
    [[nodiscard]] std::uintmax_t size() const override;
    [[nodiscard]] Identifiers starting_with(const HashPrefix& prefix) const override;

private:
    mutable std::map<std::string, Data> m_data;
};

bool MemoryStore::has(const Identifier& identifier) const {
    return m_data.find(identifier.to_string()) != m_data.end();
}

std::optional<IDataSource::Data> MemoryStore::get(const Identifier& identifier) const {
    const auto found = m_data.find(identifier.to_string());

    if (found == m_data.end()) {
        return std::nullopt;
    }

    return found->second;
}

void MemoryStore::put(const Identifier& identifier, const Data& body) const {
    m_data[identifier.to_string()] = body;
}

bool MemoryStore::remove(const Identifier& identifier) const {
    return m_data.erase(identifier.to_string()) > 0;
}

std::uintmax_t MemoryStore::size() const {
    std::uintmax_t total = 0;

    for (const auto& [name, body] : m_data) {
        total += body.size();
    }

    return total;
}

// The handler never lists what is held.
IDataSource::Identifiers
MemoryStore::starting_with([[maybe_unused]] const HashPrefix& prefix) const {
    return {};
}

// The identifier of hello, and the path that names it.
Identifier hello_id() {
    return {"sha256", std::string(hello_hash)};
}

std::string hello_path() {
    return "/data/sha256/" + std::string(hello_hash);
}

// hello, zlib-compressed.
std::string compressed_hello() {
    return Converter::text_of(Zlib::compress(Converter::bytes_of(hello), Zlib::max_level));
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

Request put(std::string uri, std::string body) {
    return request_of("PUT", std::move(uri), std::move(body));
}

// What handler answers request with, on connection.
Response answer(const DataHandler& handler, const Request& request,
                const ConnectionInfo& connection) {
    Response response;

    handler(request, response, connection);
    return response;
}

// What handler answers request with, on a connection that carries no
// node's signature.
Response answer(const DataHandler& handler, const Request& request) {
    return answer(handler, request, ConnectionInfo(Address(remote_host, remote_port)));
}

// The status handler answers request with.
int status_of(const DataHandler& handler, const Request& request) {
    return answer(handler, request).status_code();
}

// The type of the problem a response describes.
std::string problem_type(const Response& response) {
    return Json::parse(response.body()).at("type").string();
}

// Says each search is held off, and keeps the text of each identifier it is
// asked of.
DataHandler::IsHeldOff all_held_off(std::shared_ptr<std::vector<std::string>> asked) {
    return [asked = std::move(asked)](const Identifier& identifier) {
        asked->push_back(identifier.to_string());
        return true;
    };
}

} // namespace

// The tests read optionals after ASSERT_TRUE(x.has_value()), which this
// check does not follow.
// NOLINTBEGIN(bugprone-unchecked-optional-access)

TEST(DataHandlerTest, HeldContentIsServed) {
    const auto store = std::make_shared<MemoryStore>();
    const DataHandler handler(store);
    store->put(hello_id(), Converter::bytes_of(hello));

    const auto response = answer(handler, get(hello_path()));

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.headers().get("Content-Type"), "application/octet-stream");
    EXPECT_EQ(response.body(), "hello");
}

// Content stored compressed is served as it was stored (HttpApi §5.1).
TEST(DataHandlerTest, CompressedContentIsServedAsStored) {
    const auto store = std::make_shared<MemoryStore>();
    const DataHandler handler(store);
    store->put(hello_id(), Converter::bytes_of(compressed_hello()));

    const auto response = answer(handler, get(hello_path()));

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.body(), compressed_hello());
}

// A hash in upper case names the same content (HttpApi §5.4), and a query
// does not change what the path names.
TEST(DataHandlerTest, UpperCaseHashAndQueryAreTaken) {
    const auto store = std::make_shared<MemoryStore>();
    const DataHandler handler(store);
    const auto upper_case =
        get("/data/sha256/2CF24DBA5FB0A30E26E83B2AC5B9E29E1B161E5C1FA7425E73043362938B9824");
    store->put(hello_id(), Converter::bytes_of(hello));

    EXPECT_EQ(status_of(handler, upper_case), 200);
    EXPECT_EQ(status_of(handler, get(hello_path() + "?from=test")), 200);
}

// Content not held is looked for, and the client told when to ask again,
// in Retry-After and in the problem, and not to keep the answer (HttpApi
// §5.2, §17.2, §20).
TEST(DataHandlerTest, ContentNotHeldIsUnavailable) {
    const DataHandler handler(std::make_shared<MemoryStore>());
    Channel not_found(DataNotFound::name);

    const auto response = answer(handler, get(hello_path()));
    const auto posted = not_found.next<DataNotFound>(0ms);
    const auto problem = Json::parse(response.body());

    EXPECT_EQ(response.status_code(), 503);
    EXPECT_EQ(response.headers().get("Content-Type"), "application/problem+json");
    EXPECT_EQ(response.headers().get("Retry-After"), "5");
    EXPECT_EQ(response.headers().get("Cache-Control"), "no-store");
    EXPECT_EQ(problem.at("type").string(), "https://libranet.org/problems/content-unavailable");
    EXPECT_EQ(problem.at("status").integer(), 503);
    EXPECT_EQ(problem.at("retry_after").integer(), 5);
    ASSERT_TRUE(posted.has_value());
    EXPECT_EQ(posted->identifier.to_string(), "sha256/" + std::string(hello_hash));
    EXPECT_TRUE(not_found.empty());
}

// The client is told to ask again after network.retry_after_seconds.
TEST(DataHandlerTest, ContentNotHeldIsRetriedAfterTheSettingsSay) {
    const auto store = std::make_shared<MemoryStore>();
    const DataHandler later(store, DataHandler::default_content_bytes, 30s);
    const DataHandler at_once(store, DataHandler::default_content_bytes, 0s);

    const auto response = answer(later, get(hello_path()));
    const auto immediate = answer(at_once, get(hello_path()));

    EXPECT_EQ(response.status_code(), 503);
    EXPECT_EQ(response.headers().get("Retry-After"), "30");
    EXPECT_EQ(Json::parse(response.body()).at("retry_after").integer(), 30);
    EXPECT_EQ(immediate.status_code(), 503);
    EXPECT_EQ(immediate.headers().get("Retry-After"), "0");
    EXPECT_EQ(Json::parse(immediate.body()).at("retry_after").integer(), 0);
}

// While the search for content is held off, the node will not look for it,
// so it is not found (HttpApi §5.3), and not posted as needed; but it is
// posted as asked for.
TEST(DataHandlerTest, ContentHeldOffIsNotFound) {
    const auto asked = std::make_shared<std::vector<std::string>>();
    const DataHandler handler(std::make_shared<MemoryStore>(), DataHandler::default_content_bytes,
                              DataHandler::default_retry_after, all_held_off(asked));
    Channel not_found(DataNotFound::name);
    Channel requested(DataRequested::name);

    const auto response = answer(handler, get(hello_path()));

    EXPECT_EQ(response.status_code(), 404);
    EXPECT_EQ(response.headers().get("Content-Type"), "application/problem+json");
    EXPECT_FALSE(response.headers().has("Retry-After"));
    EXPECT_EQ(problem_type(response), "about:blank");
    EXPECT_EQ(*asked, std::vector<std::string>{hello_id().to_string()});
    EXPECT_TRUE(not_found.empty());
    EXPECT_TRUE(requested.next<DataRequested>(0ms).has_value());
}

// Only the search for the content asked for is held off. The function
// captures nothing, so that moving it cannot throw.
TEST(DataHandlerTest, OnlyContentHeldOffIsNotFound) {
    const auto world_id = Identifier::from(Converter::bytes_of("world"));
    const DataHandler handler(std::make_shared<MemoryStore>(), DataHandler::default_content_bytes,
                              DataHandler::default_retry_after,
                              [](const Identifier& identifier) {
        return identifier.hash() == hello_hash;
    });

    EXPECT_EQ(status_of(handler, get(hello_path())), 404);
    EXPECT_EQ(status_of(handler, get("/data/" + world_id.to_string())), 503);
}

// Content held is served whether or not a search for it would be held off,
// which is not asked.
TEST(DataHandlerTest, HeldContentIsServedWhileHeldOff) {
    const auto store = std::make_shared<MemoryStore>();
    const auto asked = std::make_shared<std::vector<std::string>>();
    const DataHandler handler(store, DataHandler::default_content_bytes,
                              DataHandler::default_retry_after, all_held_off(asked));
    store->put(hello_id(), Converter::bytes_of(hello));

    const auto response = answer(handler, get(hello_path()));

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.body(), "hello");
    EXPECT_TRUE(asked->empty());
}

TEST(DataHandlerTest, NegativeRetryAfterIsRefused) {
    const auto store = std::make_shared<MemoryStore>();

    try {
        const DataHandler refused(store, DataHandler::default_content_bytes, -1s);
        FAIL() << "A retry after of -1s was taken";
    } catch (const std::invalid_argument& error) {
        EXPECT_STREQ(error.what(), "network.retry_after_seconds is less than zero");
    }
}

TEST(DataHandlerTest, HeldContentIsNotPostedAsNotFound) {
    const auto store = std::make_shared<MemoryStore>();
    const DataHandler handler(store);
    Channel not_found(DataNotFound::name);
    store->put(hello_id(), Converter::bytes_of(hello));

    (void)answer(handler, get(hello_path()));

    EXPECT_TRUE(not_found.empty());
}

// Each GET is posted as a request for the content, held or not, and once.
TEST(DataHandlerTest, EachGetIsPostedAsRequested) {
    const auto store = std::make_shared<MemoryStore>();
    const DataHandler handler(store);
    Channel requested(DataRequested::name);

    (void)answer(handler, get(hello_path()));
    store->put(hello_id(), Converter::bytes_of(hello));
    (void)answer(handler, get(hello_path()));

    const auto missing = requested.next<DataRequested>(0ms);
    const auto held = requested.next<DataRequested>(0ms);

    ASSERT_TRUE(missing.has_value());
    EXPECT_EQ(missing->identifier.to_string(), hello_id().to_string());
    ASSERT_TRUE(held.has_value());
    EXPECT_EQ(held->identifier.to_string(), hello_id().to_string());
    EXPECT_TRUE(requested.empty());
}

// Only a GET of content is a request for it: not a PUT, nor a request that
// names no content.
TEST(DataHandlerTest, OnlyAGetOfContentIsPostedAsRequested) {
    const DataHandler handler(std::make_shared<MemoryStore>());
    Channel requested(DataRequested::name);

    (void)answer(handler, put(hello_path(), std::string(hello)));
    (void)answer(handler, request_of("POST", hello_path()));
    (void)answer(handler, get("/data/sha256/x" + std::string(hello_hash.substr(1))));
    (void)answer(handler, get("/data/search/2cf"));

    EXPECT_TRUE(requested.empty());
}

// A hash that is not hex, or not as many digits as SHA-256's, an algorithm
// the node does not know, and ".." as one, which named the directory above
// a store (issue #782), each name no content identifier (HttpApi §5.4).
TEST(DataHandlerTest, InvalidIdentifierIsBadRequest) {
    const auto store = std::make_shared<MemoryStore>();
    const DataHandler handler(store);
    const auto short_hash = std::string(hello_hash.substr(1));
    const auto not_hex = "x" + short_hash;

    const auto response = answer(handler, get("/data/sha256/" + not_hex));

    EXPECT_EQ(response.status_code(), 400);
    EXPECT_EQ(problem_type(response), "https://libranet.org/problems/invalid-content-address");
    EXPECT_EQ(status_of(handler, get("/data/sha256/" + short_hash)), 400);
    EXPECT_EQ(status_of(handler, get(hello_path() + "0")), 400);
    EXPECT_EQ(status_of(handler, get("/data/md5/" + std::string(hello_hash))), 400);
    EXPECT_EQ(status_of(handler, get("/data/../" + std::string(hello_hash))), 400);
    EXPECT_EQ(status_of(handler, put("/data/sha256/" + not_hex, std::string(hello))), 400);
}

// The handler is registered for /data, so it sees whatever is beneath it:
// a path of another form, or beneath another endpoint, names no content,
// whatever the method.
TEST(DataHandlerTest, OtherPathsAreNotFound) {
    const DataHandler handler(std::make_shared<MemoryStore>());

    const auto response = answer(handler, get("/data"));

    EXPECT_EQ(response.status_code(), 404);
    EXPECT_EQ(problem_type(response), "about:blank");
    EXPECT_EQ(status_of(handler, get("/data/")), 404);
    EXPECT_EQ(status_of(handler, get("/data/sha256")), 404);
    EXPECT_EQ(status_of(handler, get("/data/sha256/")), 404);
    EXPECT_EQ(status_of(handler, get("/data//" + std::string(hello_hash))), 404);
    EXPECT_EQ(status_of(handler, get(hello_path() + "/index.html")), 404);
    EXPECT_EQ(status_of(handler, get("/data/search/2cf")), 404);
    EXPECT_EQ(status_of(handler, get("/data/karma/head")), 404);
    EXPECT_EQ(status_of(handler, get("/database")), 404);
    EXPECT_EQ(status_of(handler, request_of("POST", "/data/nodes/x")), 404);
}

TEST(DataHandlerTest, OtherMethodsAreNotAllowed) {
    const DataHandler handler(std::make_shared<MemoryStore>());

    const auto response = answer(handler, request_of("POST", hello_path()));

    EXPECT_EQ(response.status_code(), 405);
    EXPECT_EQ(response.headers().get("Allow"), "GET, PUT");
    EXPECT_EQ(response.headers().get("Content-Type"), "application/problem+json");
    EXPECT_EQ(status_of(handler, request_of("DELETE", hello_path())), 405);
    EXPECT_EQ(status_of(handler, request_of("HEAD", hello_path())), 405);
    EXPECT_EQ(status_of(handler, request_of("POST", "/data/md5/x")), 405);
}

TEST(DataHandlerTest, PutStoresContent) {
    const auto store = std::make_shared<MemoryStore>();
    const DataHandler handler(store);
    Channel stored(DataStored::name);

    const auto response = answer(handler, put(hello_path(), std::string(hello)));
    const auto posted = stored.next<DataStored>(0ms);

    EXPECT_EQ(response.status_code(), 201);
    EXPECT_EQ(store->get(hello_id()), Converter::bytes_of(hello));
    ASSERT_TRUE(posted.has_value());
    EXPECT_EQ(posted->identifier.to_string(), "sha256/" + std::string(hello_hash));
    EXPECT_FALSE(posted->node.has_value());
    EXPECT_EQ(posted->size, 5U);
    EXPECT_TRUE(stored.empty());
}

// Content sent compressed is stored as it came, under the identifier of
// what it decompresses to (HttpApi §7.1, §8).
TEST(DataHandlerTest, PutStoresCompressedContentAsReceived) {
    const auto store = std::make_shared<MemoryStore>();
    const DataHandler handler(store);
    Channel stored(DataStored::name);

    const auto response = answer(handler, put(hello_path(), compressed_hello()));
    const auto posted = stored.next<DataStored>(0ms);

    EXPECT_EQ(response.status_code(), 201);
    EXPECT_EQ(store->get(hello_id()), Converter::bytes_of(compressed_hello()));
    ASSERT_TRUE(posted.has_value());
    EXPECT_EQ(posted->size, compressed_hello().size());
}

// What is stored is named as coming from the node whose signature the
// connection carries.
TEST(DataHandlerTest, PutNamesTheNodeItCameFrom) {
    const DataHandler handler(std::make_shared<MemoryStore>());
    ConnectionInfo connection(Address(remote_host, remote_port));
    Channel stored(DataStored::name);
    connection.set_node(Identifier("sha256", std::string(node_hash)));

    (void)answer(handler, put(hello_path(), std::string(hello)), connection);
    const auto posted = stored.next<DataStored>(0ms);

    ASSERT_TRUE(posted.has_value());
    ASSERT_TRUE(posted->node.has_value());
    EXPECT_EQ(posted->node->to_string(), "sha256/" + std::string(node_hash));
}

// Content already held is not stored again, nor posted as stored, however
// it is sent.
TEST(DataHandlerTest, PutOfContentHeldIsOk) {
    const auto store = std::make_shared<MemoryStore>();
    const DataHandler handler(store);
    Channel stored(DataStored::name);
    store->put(hello_id(), Converter::bytes_of(hello));

    const auto response = answer(handler, put(hello_path(), compressed_hello()));

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.body(), "");
    EXPECT_EQ(store->get(hello_id()), Converter::bytes_of(hello));
    EXPECT_TRUE(stored.empty());
}

TEST(DataHandlerTest, PutOfOtherContentIsBadRequest) {
    const auto store = std::make_shared<MemoryStore>();
    const DataHandler handler(store);
    Channel stored(DataStored::name);
    store->put(hello_id(), Converter::bytes_of(hello));

    const auto response = answer(handler, put(hello_path(), "world"));
    const auto empty = answer(handler, put(hello_path(), ""));

    EXPECT_EQ(response.status_code(), 400);
    EXPECT_EQ(problem_type(response), "about:blank");
    EXPECT_EQ(empty.status_code(), 400);
    EXPECT_EQ(store->get(hello_id()), Converter::bytes_of(hello));
    EXPECT_TRUE(stored.empty());
}

// A body of 1 MiB is taken, and one larger is refused (HttpApi §7.1).
TEST(DataHandlerTest, PutOverOneMebibyteIsTooLarge) {
    const auto store = std::make_shared<MemoryStore>();
    const DataHandler handler(store);
    Channel stored(DataStored::name);
    const std::string largest(DataHandler::max_body_bytes, 'a');
    const auto largest_id = Identifier::from(Converter::bytes_of(largest));
    const auto too_large = largest + "a";

    const auto refused =
        answer(handler, put("/data/" + Identifier::from(Converter::bytes_of(too_large)).to_string(),
                            too_large));
    const auto taken = answer(handler, put("/data/" + largest_id.to_string(), largest));

    EXPECT_EQ(refused.status_code(), 413);
    EXPECT_EQ(problem_type(refused), "https://libranet.org/problems/content-too-large");
    EXPECT_EQ(taken.status_code(), 201);
    EXPECT_TRUE(store->has(largest_id));
    EXPECT_EQ(store->size(), DataHandler::max_body_bytes);
}

// A body that would decompress to more than the handler takes is refused,
// as one that is not the content is; the limit bounds only decompressing.
TEST(DataHandlerTest, PutDecompressingPastTheLimitIsRefused) {
    const auto store = std::make_shared<MemoryStore>();
    const DataHandler cramped(store, hello.size() - 1);
    const DataHandler roomy(store, hello.size());

    EXPECT_EQ(status_of(cramped, put(hello_path(), compressed_hello())), 400);
    EXPECT_FALSE(store->has(hello_id()));
    EXPECT_EQ(status_of(roomy, put(hello_path(), compressed_hello())), 201);
    EXPECT_TRUE(store->remove(hello_id()));
    EXPECT_EQ(status_of(cramped, put(hello_path(), std::string(hello))), 201);
}

// NOLINTEND(bugprone-unchecked-optional-access)
