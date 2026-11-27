#include <gtest/gtest.h>
#include <stdexcept>
#include <string>

#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "types/Json.hpp"

using http::Problem;
using http::Request;
using http::Response;
using http::ResponseCode;
using types::Json;

namespace {

// The URI and title of a problem of type, a space between them.
std::string uri_and_title(Problem::Type type) {
    const Problem problem(type, ResponseCode::Status::BadRequest, "detail");

    return problem.type_uri() + " " + std::string(problem.title());
}

// Whether a problem may not have status.
bool refused(ResponseCode::Status status) {
    try {
        (void)Problem(status, "detail");
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

} // namespace

TEST(ProblemTest, WithNoTypeIsAboutBlankTitledWithTheReasonPhrase) {
    const Problem problem(ResponseCode::Status::NotFound, "No resource exists at this path.");

    EXPECT_FALSE(problem.type().has_value());
    EXPECT_EQ(problem.type_uri(), "about:blank");
    EXPECT_EQ(problem.title(), "Not Found");
    EXPECT_EQ(problem.status(), ResponseCode::Status::NotFound);
    EXPECT_EQ(problem.detail(), "No resource exists at this path.");
}

TEST(ProblemTest, WithATypeIsTitledAsThatType) {
    const Problem problem(Problem::Type::InvalidContentAddress, ResponseCode::Status::BadRequest,
                          "The supplied content address is not valid.");

    EXPECT_EQ(problem.type(), Problem::Type::InvalidContentAddress);
    EXPECT_EQ(problem.type_uri(), "https://libranet.org/problems/invalid-content-address");
    EXPECT_EQ(problem.title(), "Invalid content address");
    EXPECT_EQ(problem.status(), ResponseCode::Status::BadRequest);
    EXPECT_EQ(problem.detail(), "The supplied content address is not valid.");
}

// The types and titles of HTTP API §17.2, and of §13.1's example for
// bundle-authentication-required.
TEST(ProblemTest, EachTypeHasTheUriAndTitleOfTheSpecification) {
    EXPECT_EQ(uri_and_title(Problem::Type::InvalidContentAddress),
              "https://libranet.org/problems/invalid-content-address Invalid content address");
    EXPECT_EQ(uri_and_title(Problem::Type::InvalidSearchPrefix),
              "https://libranet.org/problems/invalid-search-prefix Invalid search prefix");
    EXPECT_EQ(uri_and_title(Problem::Type::ContentUnavailable),
              "https://libranet.org/problems/content-unavailable Content temporarily unavailable");
    EXPECT_EQ(uri_and_title(Problem::Type::ContentTooLarge),
              "https://libranet.org/problems/content-too-large Content too large");
    EXPECT_EQ(uri_and_title(Problem::Type::SignatureRequired),
              "https://libranet.org/problems/signature-required Signature required");
    EXPECT_EQ(uri_and_title(Problem::Type::InvalidSignature),
              "https://libranet.org/problems/invalid-signature Invalid signature");
    EXPECT_EQ(uri_and_title(Problem::Type::InvalidList),
              "https://libranet.org/problems/invalid-list Invalid list");
    EXPECT_EQ(uri_and_title(Problem::Type::CredentialRequired),
              "https://libranet.org/problems/credential-required Credential required");
    EXPECT_EQ(uri_and_title(Problem::Type::InvalidRequest),
              "https://libranet.org/problems/invalid-request Invalid request");
    EXPECT_EQ(uri_and_title(Problem::Type::UnusableBundle),
              "https://libranet.org/problems/unusable-bundle Unusable bundle");
    EXPECT_EQ(uri_and_title(Problem::Type::BundleAuthenticationRequired),
              "https://libranet.org/problems/bundle-authentication-required"
              " Authentication required");
    EXPECT_EQ(uri_and_title(Problem::Type::SessionRequired),
              "https://libranet.org/problems/session-required Session required");
    EXPECT_EQ(uri_and_title(Problem::Type::InvalidBundleSignature),
              "https://libranet.org/problems/invalid-bundle-signature Invalid bundle signature");
    EXPECT_EQ(uri_and_title(Problem::Type::WrongKey),
              "https://libranet.org/problems/wrong-key Wrong key");
}

TEST(ProblemTest, StatusThatIsNotAnErrorIsRefused) {
    EXPECT_TRUE(refused(ResponseCode::Status::Continue));
    EXPECT_TRUE(refused(ResponseCode::Status::Ok));
    EXPECT_TRUE(refused(ResponseCode::Status::Found));
    EXPECT_FALSE(refused(ResponseCode::Status::BadRequest));
    EXPECT_FALSE(refused(ResponseCode::Status::InternalServerError));
}

TEST(ProblemTest, TypeThatNamesNoneIsRefused) {
    constexpr auto named_by_none = 255;
    // Out of range: the point of the test.
    // NOLINTNEXTLINE(clang-analyzer-optin.core.EnumCastOutOfRange)
    const auto type = static_cast<Problem::Type>(named_by_none);

    EXPECT_THROW((void)Problem(type, ResponseCode::Status::BadRequest, "detail"),
                 std::invalid_argument);
}

// HTTP API §17.1's example, without the instance it names.
TEST(ProblemTest, JsonHasTheTypeTitleStatusAndDetail) {
    const Problem problem(Problem::Type::InvalidContentAddress, ResponseCode::Status::BadRequest,
                          "The supplied content address is not valid.");

    EXPECT_EQ(problem.to_json(), Json::parse(R"({
        "type": "https://libranet.org/problems/invalid-content-address",
        "title": "Invalid content address",
        "status": 400,
        "detail": "The supplied content address is not valid."
    })"));
}

// The status, its reason phrase, the Content-Type, and the body are the
// problem's, whatever the response held, and its other headers are kept.
TEST(ProblemTest, AnswerWritesTheProblemAsProblemJson) {
    const Problem problem(ResponseCode::Status::NotFound, "No resource exists at this path.");
    Response response;

    response.set_reason_phrase("Fine");
    response.headers().set("Content-Type", "text/plain");
    response.headers().set("X-Request-Path", "/missing");
    response.set_body("Not Found\r\n");
    problem.answer(response);

    EXPECT_EQ(response.status_code(), 404);
    EXPECT_EQ(response.reason_phrase(), "Not Found");
    EXPECT_EQ(response.headers().get("Content-Type"), "application/problem+json");
    EXPECT_EQ(response.headers().get("X-Request-Path"), "/missing");
    EXPECT_EQ(Json::parse(response.body()), problem.to_json());
}

TEST(ProblemTest, NotFoundSaysNoResourceIsAtThePath) {
    Response response;

    Problem::answer_not_found(response);

    EXPECT_EQ(response.status_code(), 404);
    EXPECT_EQ(response.reason_phrase(), "Not Found");
    EXPECT_EQ(response.headers().get("Content-Type"), "application/problem+json");
    EXPECT_EQ(Json::parse(response.body()), Json::parse(R"({
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "No resource exists at this path."
    })"));
}

TEST(ProblemTest, MethodNotAllowedNamesTheMethodsThePathTakes) {
    Request request;
    Response response;

    request.set_method("DELETE");
    Problem::answer_method_not_allowed(request, "GET, PUT", response);

    EXPECT_EQ(response.status_code(), 405);
    EXPECT_EQ(response.reason_phrase(), "Method Not Allowed");
    EXPECT_EQ(response.headers().get("Allow"), "GET, PUT");
    EXPECT_EQ(response.headers().get("Content-Type"), "application/problem+json");
    EXPECT_EQ(Json::parse(response.body()), Json::parse(R"({
        "type": "about:blank",
        "title": "Method Not Allowed",
        "status": 405,
        "detail": "DELETE is not supported for this resource."
    })"));
}

// A method is the client's text, which the body holds as a JSON string.
TEST(ProblemTest, MethodNotAllowedEscapesTheMethod) {
    Request request;
    Response response;

    request.set_method(R"(GE"T\)");
    Problem::answer_method_not_allowed(request, "GET", response);

    EXPECT_EQ(Json::parse(response.body()).at("detail").string(),
              R"(GE"T\ is not supported for this resource.)");
}
