#include <gtest/gtest.h>
#include <filesystem>
#include <memory>
#include <string>

#include "Fixtures.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "storage/DataDir.hpp"
#include "storage/HttpHandler.hpp"
#include "storage/Identifier.hpp"

using fixtures::bytes_of;
using fixtures::hello;
using fixtures::hello_hash;
using fixtures::MemorySource;
using fixtures::ScratchDirectory;
using fixtures::write_file;
using http::Request;
using http::Response;
using storage::DataDir;
using storage::HttpHandler;
using storage::Identifier;

namespace {

// A GET request for uri, from no node.
Request unsigned_get(const std::string& uri) {
    Request request;

    request.set_method("GET");
    request.set_uri(uri);
    return request;
}

// A GET request for uri, from a node: one with its signature.
Request signed_get(const std::string& uri) {
    auto request = unsigned_get(uri);

    request.headers().set("X-Node-Signature", "signature");
    return request;
}

// What handler answers to request.
Response answer(const HttpHandler& handler, const Request& request) {
    Response response;

    handler(request, response);
    return response;
}

} // namespace

// A search needs no signature, and finds nothing yet.
TEST(HttpHandlerTest, SearchFindsNothing) {
    const HttpHandler handler(std::make_shared<MemorySource>());

    const auto response = answer(handler, unsigned_get("/data/search/2cf"));

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.headers().get("Content-Type"), "application/json");
    EXPECT_EQ(response.body(), "{\"matches\": []}\r\n");
}

TEST(HttpHandlerTest, DataForNoNodeIsUnauthorized) {
    const auto source = std::make_shared<MemorySource>();
    const HttpHandler handler(source);
    source->put(Identifier("sha256", hello_hash), bytes_of(hello));

    const auto response = answer(handler, unsigned_get(std::string("/data/sha256/") + hello_hash));

    EXPECT_EQ(response.status_code(), 401);
    EXPECT_EQ(response.body(), "Unauthorized: Node ID signature missing\r\n");
}

TEST(HttpHandlerTest, DataHeldIsServed) {
    const auto source = std::make_shared<MemorySource>();
    const HttpHandler handler(source);
    source->put(Identifier("sha256", hello_hash), bytes_of(hello));

    const auto response = answer(handler, signed_get(std::string("/data/sha256/") + hello_hash));

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.headers().get("Content-Type"), "application/octet-stream");
    EXPECT_EQ(response.body(), "hello");
}

TEST(HttpHandlerTest, DataNotHeldIsUnavailable) {
    const HttpHandler handler(std::make_shared<MemorySource>());

    const auto response = answer(handler, signed_get(std::string("/data/sha256/") + hello_hash));

    EXPECT_EQ(response.status_code(), 503);
    EXPECT_EQ(response.body(), "Service Unavailable: Fetching content from other nodes\r\n");
}

// ".." as the algorithm named the directory above the store, so the file
// there was served (issue #782).
TEST(HttpHandlerTest, UnsupportedAlgorithmIsBadRequest) {
    const ScratchDirectory directory;
    const HttpHandler handler(DataDir::create(directory.path() / "store"));

    std::filesystem::create_directories(directory.path() / "store");
    write_file(directory.path() / "2cf" / hello_hash, hello);

    const auto response = answer(handler, signed_get(std::string("/data/../") + hello_hash));

    EXPECT_EQ(response.status_code(), 400);
    EXPECT_EQ(response.body(), "Bad Request: Invalid content identifier\r\n");
}

TEST(HttpHandlerTest, DataWithoutAHashIsBadRequest) {
    const HttpHandler handler(std::make_shared<MemorySource>());

    const auto response = answer(handler, signed_get("/data/sha256"));

    EXPECT_EQ(response.status_code(), 400);
    EXPECT_EQ(response.body(), "Bad Request: Invalid content identifier\r\n");
}

// The handler is registered for /data, so it sees whatever starts with it.
TEST(HttpHandlerTest, OtherPathsAreNotFound) {
    const HttpHandler handler(std::make_shared<MemorySource>());

    const auto data = answer(handler, signed_get("/data"));
    const auto other = answer(handler, signed_get("/database"));

    EXPECT_EQ(data.status_code(), 404);
    EXPECT_EQ(data.body(), "Not Found\r\n");
    EXPECT_EQ(other.status_code(), 404);
    EXPECT_EQ(other.body(), "Not Found\r\n");
}
