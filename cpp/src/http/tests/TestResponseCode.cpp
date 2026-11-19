#include <gtest/gtest.h>
#include <array>
#include <cstdint>
#include <sstream>
#include <string_view>

#include "http/ResponseCode.hpp"

using http::ResponseCode;
using Status = ResponseCode::Status;

namespace {

// Codes at the edges of the classes of codes, which no status names.
constexpr std::uint16_t below_informational = 99;
constexpr std::uint16_t last_informational = 199;
constexpr std::uint16_t last_success = 299;
constexpr std::uint16_t last_redirection = 399;
constexpr std::uint16_t last_client_error = 499;
constexpr std::uint16_t last_server_error = 599;
constexpr std::uint16_t above_server_error = 600;

// A status and its reason phrase.
struct Phrase {
    Status status = Status::Ok;
    std::string_view phrase;
};

// Each status, with the reason phrase the IANA HTTP Status Code Registry
// gives it from the RFC that defines it, but for three: 413 and 422 keep the
// names RFC 7231 and RFC 4918 gave them, before RFC 9110 renamed them
// Content Too Large and Unprocessable Content, and 418 keeps RFC 2324's.
constexpr std::array phrases{
    Phrase{Status::Continue, "Continue"},
    Phrase{Status::SwitchingProtocols, "Switching Protocols"},
    Phrase{Status::Processing, "Processing"},
    Phrase{Status::EarlyHints, "Early Hints"},
    Phrase{Status::Ok, "OK"},
    Phrase{Status::Created, "Created"},
    Phrase{Status::Accepted, "Accepted"},
    Phrase{Status::NonAuthoritativeInformation, "Non-Authoritative Information"},
    Phrase{Status::NoContent, "No Content"},
    Phrase{Status::ResetContent, "Reset Content"},
    Phrase{Status::PartialContent, "Partial Content"},
    Phrase{Status::MultiStatus, "Multi-Status"},
    Phrase{Status::MultipleChoices, "Multiple Choices"},
    Phrase{Status::MovedPermanently, "Moved Permanently"},
    Phrase{Status::Found, "Found"},
    Phrase{Status::SeeOther, "See Other"},
    Phrase{Status::NotModified, "Not Modified"},
    Phrase{Status::UseProxy, "Use Proxy"},
    Phrase{Status::TemporaryRedirect, "Temporary Redirect"},
    Phrase{Status::PermanentRedirect, "Permanent Redirect"},
    Phrase{Status::BadRequest, "Bad Request"},
    Phrase{Status::Unauthorized, "Unauthorized"},
    Phrase{Status::PaymentRequired, "Payment Required"},
    Phrase{Status::Forbidden, "Forbidden"},
    Phrase{Status::NotFound, "Not Found"},
    Phrase{Status::MethodNotAllowed, "Method Not Allowed"},
    Phrase{Status::NotAcceptable, "Not Acceptable"},
    Phrase{Status::ProxyAuthenticationRequired, "Proxy Authentication Required"},
    Phrase{Status::RequestTimeout, "Request Timeout"},
    Phrase{Status::Conflict, "Conflict"},
    Phrase{Status::Gone, "Gone"},
    Phrase{Status::LengthRequired, "Length Required"},
    Phrase{Status::PreconditionFailed, "Precondition Failed"},
    Phrase{Status::PayloadTooLarge, "Payload Too Large"},
    Phrase{Status::UriTooLong, "URI Too Long"},
    Phrase{Status::UnsupportedMediaType, "Unsupported Media Type"},
    Phrase{Status::RangeNotSatisfiable, "Range Not Satisfiable"},
    Phrase{Status::ExpectationFailed, "Expectation Failed"},
    Phrase{Status::ImATeapot, "I'm a teapot"},
    Phrase{Status::UnprocessableEntity, "Unprocessable Entity"},
    Phrase{Status::TooManyRequests, "Too Many Requests"},
    Phrase{Status::InternalServerError, "Internal Server Error"},
    Phrase{Status::NotImplemented, "Not Implemented"},
    Phrase{Status::BadGateway, "Bad Gateway"},
    Phrase{Status::ServiceUnavailable, "Service Unavailable"},
    Phrase{Status::GatewayTimeout, "Gateway Timeout"},
    Phrase{Status::HttpVersionNotSupported, "HTTP Version Not Supported"},
    Phrase{Status::VariantAlsoNegotiates, "Variant Also Negotiates"},
    Phrase{Status::InsufficientStorage, "Insufficient Storage"},
    Phrase{Status::LoopDetected, "Loop Detected"},
    Phrase{Status::NotExtended, "Not Extended"},
    Phrase{Status::NetworkAuthenticationRequired, "Network Authentication Required"},
};

} // namespace

// Each test checks one helper at the edges of its class: the code before
// the class's first, its first and last, and the code after its last.

TEST(ResponseCodeTest, IsInformational) {
    EXPECT_FALSE(ResponseCode(below_informational).is_informational());
    EXPECT_TRUE(ResponseCode(Status::Continue).is_informational());
    EXPECT_TRUE(ResponseCode(last_informational).is_informational());
    EXPECT_FALSE(ResponseCode(Status::Ok).is_informational());
}

TEST(ResponseCodeTest, IsSuccess) {
    EXPECT_FALSE(ResponseCode(last_informational).is_success());
    EXPECT_TRUE(ResponseCode(Status::Ok).is_success());
    EXPECT_TRUE(ResponseCode(last_success).is_success());
    EXPECT_FALSE(ResponseCode(Status::MultipleChoices).is_success());
}

TEST(ResponseCodeTest, IsRedirection) {
    EXPECT_FALSE(ResponseCode(last_success).is_redirection());
    EXPECT_TRUE(ResponseCode(Status::MultipleChoices).is_redirection());
    EXPECT_TRUE(ResponseCode(last_redirection).is_redirection());
    EXPECT_FALSE(ResponseCode(Status::BadRequest).is_redirection());
}

TEST(ResponseCodeTest, IsClientError) {
    EXPECT_FALSE(ResponseCode(last_redirection).is_client_error());
    EXPECT_TRUE(ResponseCode(Status::BadRequest).is_client_error());
    EXPECT_TRUE(ResponseCode(last_client_error).is_client_error());
    EXPECT_FALSE(ResponseCode(Status::InternalServerError).is_client_error());
}

TEST(ResponseCodeTest, IsServerError) {
    EXPECT_FALSE(ResponseCode(last_client_error).is_server_error());
    EXPECT_TRUE(ResponseCode(Status::InternalServerError).is_server_error());
    EXPECT_TRUE(ResponseCode(last_server_error).is_server_error());
    EXPECT_FALSE(ResponseCode(above_server_error).is_server_error());
}

// Client errors and server errors are both errors.
TEST(ResponseCodeTest, IsError) {
    EXPECT_FALSE(ResponseCode(last_redirection).is_error());
    EXPECT_TRUE(ResponseCode(Status::BadRequest).is_error());
    EXPECT_TRUE(ResponseCode(last_client_error).is_error());
    EXPECT_TRUE(ResponseCode(Status::InternalServerError).is_error());
    EXPECT_TRUE(ResponseCode(last_server_error).is_error());
    EXPECT_FALSE(ResponseCode(above_server_error).is_error());
}

TEST(ResponseCodeTest, EachStatusHasItsReasonPhrase) {
    for (const auto& entry : phrases) {
        const ResponseCode code(entry.status);

        EXPECT_EQ(ResponseCode::default_reason_phrase(entry.status), entry.phrase);
        EXPECT_EQ(code.reason_phrase(), entry.phrase);
    }
}

// A code no status names is kept, with no phrase of its own.
TEST(ResponseCodeTest, UnnamedCodeIsKept) {
    const ResponseCode code(last_success);

    EXPECT_EQ(code.code(), last_success);
    EXPECT_EQ(code.reason_phrase(), "Unknown Status Code");
    EXPECT_EQ(ResponseCode::default_reason_phrase(last_success), "Unknown Status Code");
}

TEST(ResponseCodeTest, CustomReasonPhrase) {
    const ResponseCode code(Status::Ok, "Fine");

    EXPECT_EQ(code.reason_phrase(), "Fine");
    EXPECT_EQ(code, Status::Ok);
}

// Codes are equal by number, whatever their phrases.
TEST(ResponseCodeTest, ComparisonsAndOutput) {
    const ResponseCode found(Status::NotFound);
    const ResponseCode renamed(Status::NotFound, "Gone Missing");
    std::ostringstream out_stream;

    out_stream << found;

    EXPECT_EQ(found, renamed);
    EXPECT_NE(found, ResponseCode(Status::Ok));
    EXPECT_NE(found, Status::Ok);
    EXPECT_EQ(out_stream.str(), "404 Not Found");
}
