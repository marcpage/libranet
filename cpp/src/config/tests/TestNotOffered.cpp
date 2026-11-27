#include <gtest/gtest.h>
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "config/NotOffered.hpp"
#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "net/Address.hpp"
#include "types/Json.hpp"
#include "web/ConnectionInfo.hpp"

using config::NotOffered;
using http::Problem;
using http::Request;
using http::Response;
using net::Address;
using std::string;
using std::string_view;
using types::Json;
using web::ConnectionInfo;

namespace {

using Feature = NotOffered::Feature;
using Status = http::ResponseCode::Status;

constexpr string_view about_blank = "about:blank";
constexpr std::int64_t not_found = 404;

// What a path the node has nothing at is answered with.
constexpr string_view no_resource = "No resource exists at this path.";

// A client on this machine, and the port /config might take.
constexpr string_view loopback = "127.0.0.1";
constexpr Address::Port client_port = 50'000;
constexpr Address::Port config_port = 8'180;

// A feature the paths beneath are not part of, and one they are.
constexpr Feature metadata{"/config/api/metadata", "metadata", false};
constexpr Feature backups{"/config/api/backups", "backups", true};

ConnectionInfo connection() {
    return {Address(loopback, client_port), Address(loopback, config_port)};
}

Request request_of(string method, string uri) {
    Request request;

    request.set_method(std::move(method));
    request.set_uri(std::move(uri));
    return request;
}

// What the handler for feature answers to a request of method for uri.
Response answer_of(const Feature& feature, string method, string uri) {
    const NotOffered handler(feature);
    auto connected = connection();
    Response response;

    handler(request_of(std::move(method), std::move(uri)), response, connected);
    return response;
}

// Whether response is the 404 about:blank, with detail.
testing::AssertionResult is_not_found(const Response& response, string_view detail) {
    if (response.code() != Status::NotFound) {
        return testing::AssertionFailure() << "status " << response.status_code();
    }

    if (response.headers().get("Content-Type") != Problem::content_type) {
        return testing::AssertionFailure() << "not a problem: " << response.body();
    }

    const auto problem = Json::parse(response.body());

    if (problem.at("type").string() != about_blank || problem.at("title").string() != "Not Found"
        || problem.at("status").integer() != not_found || problem.at("detail").string() != detail) {
        return testing::AssertionFailure() << response.body();
    }

    return testing::AssertionSuccess();
}

} // namespace

TEST(NotOfferedTest, SaysWhatTheNodeDoesNotOfferYet) {
    const auto response = answer_of(backups, "GET", "/config/api/backups");

    EXPECT_TRUE(is_not_found(response, "This node does not offer backups yet."));
}

TEST(NotOfferedTest, SaysSoWhateverTheMethod) {
    for (const auto* const method : {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}) {
        const auto response = answer_of(metadata, method, "/config/api/metadata");

        EXPECT_TRUE(is_not_found(response, "This node does not offer metadata yet.")) << method;
    }
}

TEST(NotOfferedTest, SaysSoWhateverTheQuery) {
    const auto response = answer_of(metadata, "GET", "/config/api/metadata?fields=name");

    EXPECT_TRUE(is_not_found(response, "This node does not offer metadata yet."));
}

TEST(NotOfferedTest, SaysSoBeneathAFeatureWhosePathsBeneathAreIt) {
    for (const auto* const uri : {
             "/config/api/backups/",
             "/config/api/backups/job",
             "/config/api/backups/job/run?now=1",
         }) {
        const auto response = answer_of(backups, "POST", uri);

        EXPECT_TRUE(is_not_found(response, "This node does not offer backups yet.")) << uri;
    }
}

TEST(NotOfferedTest, HasNothingBeneathAFeatureWhosePathsBeneathAreNot) {
    for (const auto* const uri : {"/config/api/metadata/", "/config/api/metadata/name"}) {
        const auto response = answer_of(metadata, "GET", uri);

        EXPECT_TRUE(is_not_found(response, no_resource)) << uri;
    }
}

TEST(NotOfferedTest, HasNothingAtAnotherPath) {
    for (const auto* const uri : {"/config/api", "/config/api/backupsx", "/config/api/back"}) {
        const auto response = answer_of(backups, "GET", uri);

        EXPECT_TRUE(is_not_found(response, no_resource)) << uri;
    }
}

// Each path the shared /config page asks for that a later phase brings, and
// whether each path beneath it is the same feature.
TEST(NotOfferedTest, HasEachFeatureThePageUsesThatALaterPhaseBrings) {
    struct Expected {
        string_view path;
        string_view detail;
        bool beneath = false;
    };

    const std::vector<Expected> expected{
        {"/config/api/metadata", "This node does not offer metadata yet.", false},
        {"/config/api/trust", "This node does not offer trust lists yet.", true},
        {
            "/config/api/connecting",
            "This node does not offer connecting only to the nodes it trusts yet.",
            false,
        },
        {"/config/api/karma", "This node does not offer Karma yet.", true},
        {"/config/api/applications", "This node does not offer applications yet.", true},
        {"/config/api/users", "This node does not offer users yet.", false},
        {"/config/api/builds", "This node does not offer builds yet.", false},
        {"/config/api/exports", "This node does not offer exports yet.", false},
        {"/config/api/backups", "This node does not offer backups yet.", true},
        {"/config/api/restores", "This node does not offer restores yet.", false},
    };

    ASSERT_EQ(NotOffered::features.size(), expected.size());

    for (std::size_t index = 0; index < expected.size(); ++index) {
        const auto& feature = NotOffered::features.at(index);
        const auto& wanted = expected.at(index);
        const auto path = string(wanted.path);
        const auto beneath = path + "/x";

        EXPECT_EQ(feature.path, wanted.path);
        EXPECT_TRUE(is_not_found(answer_of(feature, "GET", path), wanted.detail)) << path;
        EXPECT_TRUE(is_not_found(answer_of(feature, "GET", beneath),
                                 wanted.beneath ? wanted.detail : no_resource))
            << beneath;
    }
}
