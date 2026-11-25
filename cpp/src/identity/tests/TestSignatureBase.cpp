#include <gtest/gtest.h>
#include <chrono>
#include <cstdint>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>

#include "crypto/Base64.hpp"
#include "crypto/ISignKey.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "http/StructuredField.hpp"
#include "identity/SignatureBase.hpp"

using crypto::Base64;
using crypto::ISignKey;
using http::Request;
using http::Response;
using http::StructuredField;
using identity::SignatureBase;

namespace {

// RFC 9421 B.2's test-request.
constexpr std::string_view rfc_request =
    "POST /foo?param=Value&Pet=dog HTTP/1.1\r\n"
    "Host: example.com\r\n"
    "Date: Tue, 20 Apr 2021 02:07:55 GMT\r\n"
    "Content-Type: application/json\r\n"
    "Content-Digest: sha-512=:WZDPaVn/7XgHaAy8pmojAkGWoRx2UFChF41A2svX+T"
    "aPm+AbwAgBWnrIiYllu7BNNyealdVLvRwEmTHWXvJwew==:\r\n"
    "Content-Length: 18\r\n"
    "\r\n"
    R"({"hello": "world"})";

// RFC 9421 B.2's test-response, with the Content-Digest of its body, the
// SHA-512 of {"message": "good dog"}, which B.2.4's signature base and
// signature have. B.2 prints another.
constexpr std::string_view rfc_response =
    "HTTP/1.1 200 OK\r\n"
    "Date: Tue, 20 Apr 2021 02:07:56 GMT\r\n"
    "Content-Type: application/json\r\n"
    "Content-Digest: sha-512=:mEWXIS7MaLRuGgxOBdODa3xqM1XdEvxoYhvlCFJ4"
    "1QJgJc4GTsPp29l5oGX69wWdXymyU0rjJuahq4l5aGgfLQ==:\r\n"
    "Content-Length: 23\r\n"
    "\r\n"
    R"({"message": "good dog"})";

// RFC 9421 B.1.4's test-key-ed25519, which signed B.2.6, and the Python
// node's signatures below.
constexpr std::string_view rfc_ed25519_public_key =
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEAJrQLj5P/89iXES9+vFgrIy29clF9CC/oPPsw3c5D0bs=\n"
    "-----END PUBLIC KEY-----\n";

// The Python node's signatures, which its MessageSigner made with
// http-message-signatures 2.0.1, test-key-ed25519, and its clock at
// python_created, run with PYTHONPATH=python/src as:
//
//   identity = NodeIdentity.from_private_key(
//       load_pem_private_key(test_key_ed25519_pem, None), "sha256")
//   signer = MessageSigner(identity, clock=lambda: 1757080000)
//   signer.sign_request("PUT", hello_path,
//                       {"Content-Type": "application/octet-stream"},
//                       b"hello")
//   signer.sign_request("GET", "/data/seek", {})
//   signer.sign_response(200, {"Content-Type": "application/octet-stream"},
//                        b"hello")
//   signer.sign_response(404, {})
//
// The keyid is the identity's node id, and the Content-Digest the one it
// added for the body b"hello".
constexpr std::int64_t python_created = 1'757'080'000;
constexpr std::string_view python_keyid =
    "sha256/22ce02aa18eb1ee5f39482d0f57a6ba56f4d549f81db547f3bea2863207c8a01";
constexpr std::string_view python_digest = "sha-256=:LPJNul+wow4m6DsqxbninhsWHlwfp0JecwQzYpOLmCQ=:";
constexpr std::string_view hello_path =
    "/data/sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";

// The time seconds after the UNIX epoch.
SignatureBase::Time time_at(std::int64_t seconds) {
    return SignatureBase::Time(std::chrono::seconds(seconds));
}

// What the Python node's signatures say of themselves.
SignatureBase::Parameters python_parameters() {
    return {time_at(python_created), std::string(python_keyid), std::nullopt};
}

Request request_of(std::string_view text) {
    std::istringstream stream{std::string(text)};
    Request request;

    stream >> request;
    return request;
}

Response response_of(std::string_view text) {
    std::istringstream stream{std::string(text)};
    Response response;

    stream >> response;
    return response;
}

// The covered components and parameters that a Signature-Input value gives
// for its only label.
StructuredField::InnerList signature_input(std::string_view value) {
    const auto field = StructuredField::parse(value);

    return std::get<StructuredField::InnerList>(field.members().at(0).second);
}

// Whether test-key-ed25519 made signature, in base64, of text.
bool is_signed(std::string_view text, std::string_view signature) {
    const auto key = ISignKey::from_pem(rfc_ed25519_public_key);

    return key->verify(text, Base64::decode(signature));
}

// Signature-Input as a libranet signature with base's parameters writes it.
std::string libranet_signature_input(const SignatureBase& base) {
    StructuredField field;

    field.set("libranet", base.signature_params());
    return field.to_string();
}

// Whether SignatureBase turns down a signature on message that covers what
// the Signature-Input value signature_params gives.
template<typename Message>
bool refused(const Message& message, std::string_view signature_params) {
    try {
        (void)SignatureBase(message, signature_input(signature_params)).to_string();
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

} // namespace

// RFC 9421 §2.5's example.
TEST(SignatureBaseTest, RfcExampleRequest) {
    const SignatureBase base(request_of(rfc_request),
                             signature_input(R"(sig1=("@method" "@authority" "@path" )"
                                             R"("content-digest" "content-length" )"
                                             R"("content-type");created=1618884473)"
                                             R"(;keyid="test-key-rsa-pss")"));

    EXPECT_EQ(base.to_string(),
              "\"@method\": POST\n"
              "\"@authority\": example.com\n"
              "\"@path\": /foo\n"
              "\"content-digest\": sha-512=:WZDPaVn/7XgHaAy8pmojAkGWoRx2UFChF41A2svX"
              "+TaPm+AbwAgBWnrIiYllu7BNNyealdVLvRwEmTHWXvJwew==:\n"
              "\"content-length\": 18\n"
              "\"content-type\": application/json\n"
              "\"@signature-params\": (\"@method\" \"@authority\" \"@path\" "
              "\"content-digest\" \"content-length\" \"content-type\")"
              ";created=1618884473;keyid=\"test-key-rsa-pss\"");
}

// RFC 9421 B.2.1, which covers no component.
TEST(SignatureBaseTest, RfcMinimalSignature) {
    const SignatureBase base(request_of(rfc_request),
                             signature_input(R"(sig-b21=();created=1618884473)"
                                             R"(;keyid="test-key-rsa-pss")"
                                             R"(;nonce="b3k2pp5k7z-50gnwp.yemd")"));

    EXPECT_EQ(base.to_string(), "\"@signature-params\": ();created=1618884473"
                                ";keyid=\"test-key-rsa-pss\";nonce=\"b3k2pp5k7z-50gnwp.yemd\"");
}

// RFC 9421 B.2.4.
TEST(SignatureBaseTest, RfcResponse) {
    const SignatureBase base(response_of(rfc_response),
                             signature_input(R"(sig-b24=("@status" "content-type" )"
                                             R"("content-digest" "content-length"))"
                                             R"(;created=1618884473;keyid="test-key-ecc-p256")"));

    EXPECT_EQ(base.to_string(),
              "\"@status\": 200\n"
              "\"content-type\": application/json\n"
              "\"content-digest\": sha-512=:mEWXIS7MaLRuGgxOBdODa3xqM1XdEvxoYhvlCFJ4"
              "1QJgJc4GTsPp29l5oGX69wWdXymyU0rjJuahq4l5aGgfLQ==:\n"
              "\"content-length\": 23\n"
              "\"@signature-params\": (\"@status\" \"content-type\" \"content-digest\" "
              "\"content-length\");created=1618884473;keyid=\"test-key-ecc-p256\"");
}

// RFC 9421 B.2.5.
TEST(SignatureBaseTest, RfcHmacRequest) {
    const SignatureBase base(request_of(rfc_request),
                             signature_input(R"(sig-b25=("date" "@authority" "content-type"))"
                                             R"(;created=1618884473;keyid="test-shared-secret")"));

    EXPECT_EQ(base.to_string(),
              "\"date\": Tue, 20 Apr 2021 02:07:55 GMT\n"
              "\"@authority\": example.com\n"
              "\"content-type\": application/json\n"
              "\"@signature-params\": (\"date\" \"@authority\" \"content-type\")"
              ";created=1618884473;keyid=\"test-shared-secret\"");
}

// RFC 9421 B.2.6, whose Ed25519 signature is made the same each time.
TEST(SignatureBaseTest, RfcEd25519Request) {
    const SignatureBase base(request_of(rfc_request),
                             signature_input(R"(sig-b26=("date" "@method" "@path" )"
                                             R"("@authority" "content-type" "content-length"))"
                                             R"(;created=1618884473;keyid="test-key-ed25519")"));

    EXPECT_EQ(base.to_string(),
              "\"date\": Tue, 20 Apr 2021 02:07:55 GMT\n"
              "\"@method\": POST\n"
              "\"@path\": /foo\n"
              "\"@authority\": example.com\n"
              "\"content-type\": application/json\n"
              "\"content-length\": 18\n"
              "\"@signature-params\": (\"date\" \"@method\" \"@path\" \"@authority\" "
              "\"content-type\" \"content-length\");created=1618884473"
              ";keyid=\"test-key-ed25519\"");
    EXPECT_TRUE(is_signed(base.to_string(), "wqcAqbmYJ2ji2glfAMaRy4gruYYnx2nEFN2HN6jrnDnQCK1"
                                            "u02Gb04v9EDgwUPiu4A0w6vuQv5lIp5WPpBKRCw=="));
}

// RFC 9421 §2.2.2, §2.2.3, §2.2.6, and §2.2.9's examples.
TEST(SignatureBaseTest, RfcDerivedComponents) {
    const auto request = request_of("POST /path?param=value HTTP/1.1\r\n"
                                    "Host: www.example.com\r\n\r\n");
    const auto response = response_of("HTTP/1.1 200 OK\r\n"
                                      "Date: Fri, 26 Mar 2010 00:05:00 GMT\r\n\r\n");

    const SignatureBase request_base(request,
                                     signature_input(R"(sig=("@method" "@authority" "@path"))"));
    const SignatureBase response_base(response, signature_input(R"(sig=("@status"))"));

    EXPECT_EQ(request_base.to_string(),
              "\"@method\": POST\n"
              "\"@authority\": www.example.com\n"
              "\"@path\": /path\n"
              "\"@signature-params\": (\"@method\" \"@authority\" \"@path\")");
    EXPECT_EQ(response_base.to_string(), "\"@status\": 200\n"
                                         "\"@signature-params\": (\"@status\")");
}

// RFC 9421 §2.1's examples, but for Cache-Control, which http::Headers
// keeps only the last of when it is sent twice.
TEST(SignatureBaseTest, RfcHeaderFields) {
    const auto request = request_of("GET / HTTP/1.1\r\n"
                                    "Host: www.example.com\r\n"
                                    "Date: Tue, 20 Apr 2021 02:07:56 GMT\r\n"
                                    "X-OWS-Header:   Leading and trailing whitespace.\r\n"
                                    "X-Obs-Fold-Header: Obsolete\r\n"
                                    "    line folding.\r\n"
                                    "Example-Dict:  a=1,    b=2;x=1;y=2,   c=(a   b   c)\r\n"
                                    "X-Empty-Header: \r\n\r\n");

    const SignatureBase base(request, signature_input(R"(sig=("host" "date" "x-ows-header" )"
                                                      R"("x-obs-fold-header" "example-dict" )"
                                                      R"("x-empty-header"))"));

    EXPECT_EQ(base.to_string(), "\"host\": www.example.com\n"
                                "\"date\": Tue, 20 Apr 2021 02:07:56 GMT\n"
                                "\"x-ows-header\": Leading and trailing whitespace.\n"
                                "\"x-obs-fold-header\": Obsolete line folding.\n"
                                "\"example-dict\": a=1,    b=2;x=1;y=2,   c=(a   b   c)\n"
                                "\"x-empty-header\": \n"
                                "\"@signature-params\": (\"host\" \"date\" \"x-ows-header\" "
                                "\"x-obs-fold-header\" \"example-dict\" \"x-empty-header\")");
}

TEST(SignatureBaseTest, PythonRequestWithBody) {
    Request request;

    request.set_method("PUT");
    request.set_uri(std::string(hello_path));
    request.headers().set("Content-Type", "application/octet-stream");
    request.headers().set("Content-Digest", std::string(python_digest));
    request.set_body("hello");

    const auto base = SignatureBase::of(request, python_parameters());

    EXPECT_EQ(base.to_string(),
              "\"@method\": PUT\n"
              "\"@path\": /data/sha256/"
              "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824\n"
              "\"content-digest\": sha-256=:LPJNul+wow4m6DsqxbninhsWHlwfp0JecwQzYpOLmCQ=:\n"
              "\"@signature-params\": (\"@method\" \"@path\" \"content-digest\");created=1757080000"
              ";keyid=\"sha256/22ce02aa18eb1ee5f39482d0f57a6ba56f4d549f81db547f3bea2863207c8a01\"");
    EXPECT_EQ(libranet_signature_input(base),
              "libranet=(\"@method\" \"@path\" \"content-digest\");created=1757080000"
              ";keyid=\"sha256/22ce02aa18eb1ee5f39482d0f57a6ba56f4d549f81db547f3bea2863207c8a01\"");
    EXPECT_TRUE(is_signed(base.to_string(), "qyTBq7bJhfHSzhIeP7HwXGQ4z5CMwmN9bXyFwvYymaB/vh4snx"
                                            "DXzi9CFjO3wdxYzHcNMoeYgG8OisrCzHxzBw=="));
}

TEST(SignatureBaseTest, PythonRequestWithoutBody) {
    Request request;

    request.set_method("GET");
    request.set_uri("/data/seek");

    const auto base = SignatureBase::of(request, python_parameters());

    EXPECT_EQ(libranet_signature_input(base),
              "libranet=(\"@method\" \"@path\");created=1757080000"
              ";keyid=\"sha256/22ce02aa18eb1ee5f39482d0f57a6ba56f4d549f81db547f3bea2863207c8a01\"");
    EXPECT_TRUE(is_signed(base.to_string(), "5mf0/toFaLZizh2rfro17a1BtoMCUnSeAodHbuKw5ztru0e/8S"
                                            "jh9MLT52w1Ow26lO9d52ec+4575jSoflhfDw=="));
}

TEST(SignatureBaseTest, PythonAnswerWithBody) {
    Response response;

    response.set_status_code(http::ResponseCode::Status::Ok);
    response.headers().set("Content-Type", "application/octet-stream");
    response.headers().set("Content-Digest", std::string(python_digest));
    response.set_body("hello");

    const auto base = SignatureBase::of(response, python_parameters());

    EXPECT_EQ(base.to_string(),
              "\"@status\": 200\n"
              "\"content-digest\": sha-256=:LPJNul+wow4m6DsqxbninhsWHlwfp0JecwQzYpOLmCQ=:\n"
              "\"@signature-params\": (\"@status\" \"content-digest\");created=1757080000"
              ";keyid=\"sha256/22ce02aa18eb1ee5f39482d0f57a6ba56f4d549f81db547f3bea2863207c8a01\"");
    EXPECT_TRUE(is_signed(base.to_string(), "1pVYtim0VmBNeA/zYV1Fm3yxuXm0YuiYZx/CRFfcI3x2vJNBeL"
                                            "qohetYGcwRkXnkCPkmfuzmoc0WBR7wynbBCw=="));
}

TEST(SignatureBaseTest, PythonAnswerWithoutBody) {
    Response response;

    response.set_status_code(http::ResponseCode::Status::NotFound);

    const auto base = SignatureBase::of(response, python_parameters());

    EXPECT_EQ(libranet_signature_input(base),
              "libranet=(\"@status\");created=1757080000"
              ";keyid=\"sha256/22ce02aa18eb1ee5f39482d0f57a6ba56f4d549f81db547f3bea2863207c8a01\"");
    EXPECT_TRUE(is_signed(base.to_string(), "yB156y/jTYmkK1iUwn2nHvX2DkLZ8lpURdVvLIV0kaBEXbgCTz"
                                            "E1UKywkJeZBs77BI1A2MXzMtqCIYE7MNfwDQ=="));
}

TEST(SignatureBaseTest, ExpiresFollowsKeyid) {
    constexpr std::int64_t created = 1'000;
    constexpr std::int64_t expires = 1'300;
    // Less than a second, which is left out.
    constexpr std::chrono::milliseconds part_of_a_second{999};
    Request request;

    request.set_method("GET");
    request.set_uri("/data/nodes");

    const auto base = SignatureBase::of(
        request, {time_at(created) + part_of_a_second, "sha256/ab", time_at(expires)});

    EXPECT_EQ(base.to_string(), "\"@method\": GET\n"
                                "\"@path\": /data/nodes\n"
                                "\"@signature-params\": (\"@method\" \"@path\")"
                                ";created=1000;keyid=\"sha256/ab\";expires=1300");
}

// RFC 9421 §2.2.6: no query, and nothing decoded. §2.2.3: the host in lower
// case. §2.1: the whitespace around a field's value is left out.
TEST(SignatureBaseTest, ValuesAreCanonical) {
    Request request;

    request.set_method("GET");
    request.set_uri("/data/a%2Fb?x=1");
    request.headers().set("Host", "WWW.Example.COM:8080");
    request.headers().set("X-Text", " \t value \t ");

    const SignatureBase base(request, signature_input(R"(sig=("@path" "@authority" "x-text"))"));

    EXPECT_EQ(base.to_string(), "\"@path\": /data/a%2Fb\n"
                                "\"@authority\": www.example.com:8080\n"
                                "\"x-text\": value\n"
                                "\"@signature-params\": (\"@path\" \"@authority\" \"x-text\")");
}

TEST(SignatureBaseTest, BodyWithoutDigestIsRefused) {
    Request request;
    Response response;

    request.set_method("PUT");
    request.set_uri(std::string(hello_path));
    request.set_body("hello");
    response.set_body("hello");

    EXPECT_THROW((void)SignatureBase::of(request, python_parameters()), std::invalid_argument);
    EXPECT_THROW((void)SignatureBase::of(response, python_parameters()), std::invalid_argument);
}

// RFC 9421 §2.5 fails for a component named twice, whatever its parameters.
TEST(SignatureBaseTest, ComponentNamedTwiceIsRefused) {
    const auto request = request_of(rfc_request);

    EXPECT_TRUE(refused(request, R"(sig=("@method" "@path" "@method"))"));
    EXPECT_TRUE(refused(request, R"(sig=("date" "date"))"));
    EXPECT_TRUE(refused(response_of(rfc_response), R"(sig=("@status" "@status"))"));
}

TEST(SignatureBaseTest, ComponentNotTakenIsRefused) {
    const auto request = request_of(rfc_request);
    const auto response = response_of(rfc_response);

    EXPECT_TRUE(refused(request, R"(sig=("@query"))"));
    EXPECT_TRUE(refused(request, R"(sig=("@target-uri"))"));
    EXPECT_TRUE(refused(request, R"(sig=("@signature-params"))"));
    EXPECT_TRUE(refused(request, R"(sig=("@status"))"));
    EXPECT_TRUE(refused(response, R"(sig=("@method"))"));
    EXPECT_TRUE(refused(response, R"(sig=("@path"))"));
    EXPECT_TRUE(refused(response, R"(sig=("@authority"))"));
    // Parameters of a component's own.
    EXPECT_TRUE(refused(request, R"(sig=("content-digest";sf))"));
    EXPECT_TRUE(refused(response, R"(sig=("@method";req))"));
    // Not a field's name in lower case, or not a string.
    EXPECT_TRUE(refused(request, R"(sig=("Content-Type"))"));
    EXPECT_TRUE(refused(request, R"(sig=("content type"))"));
    EXPECT_TRUE(refused(request, R"(sig=(""))"));
    EXPECT_TRUE(refused(request, R"(sig=(content-type))"));
    EXPECT_TRUE(refused(request, R"(sig=(1))"));
}

TEST(SignatureBaseTest, MissingFieldIsRefused) {
    const auto request = request_of(rfc_request);
    Request without_host;

    without_host.set_method("GET");
    without_host.set_uri("/");

    EXPECT_TRUE(refused(request, R"(sig=("x-missing"))"));
    EXPECT_TRUE(refused(without_host, R"(sig=("@authority"))"));
}

// RFC 9421 §2: no newline, and §2.5: nothing that is not ASCII.
TEST(SignatureBaseTest, UnprintableValueIsRefused) {
    auto request = request_of(rfc_request);

    request.headers().set("X-Line", "one\ntwo");
    request.headers().set("X-Text", "caf\xC3\xA9");
    request.headers().set("X-Control", std::string("a\0b", 3));

    EXPECT_TRUE(refused(request, R"(sig=("x-line"))"));
    EXPECT_TRUE(refused(request, R"(sig=("x-text"))"));
    EXPECT_TRUE(refused(request, R"(sig=("x-control"))"));
}

TEST(SignatureBaseTest, UnprintableResponseValueIsRefused) {
    auto response = response_of(rfc_response);

    response.headers().set("X-Delete", std::string{'a', '\x7F', 'b'});

    EXPECT_TRUE(refused(response, R"(sig=("x-delete"))"));
}

// RFC 9421 §2.1: a field's value may hold a tab.
TEST(SignatureBaseTest, TabInValueIsKept) {
    auto request = request_of(rfc_request);

    request.headers().set("X-Tab", "a\tb");

    EXPECT_EQ(SignatureBase(request, signature_input(R"(sig=("x-tab"))")).to_string(),
              "\"x-tab\": a\tb\n\"@signature-params\": (\"x-tab\")");
}

// A target in asterisk form has no path, and one in absolute form is not
// sent between nodes.
TEST(SignatureBaseTest, TargetWithoutPathIsRefused) {
    Request request;

    request.set_method("OPTIONS");
    request.set_uri("*");

    EXPECT_TRUE(refused(request, R"(sig=("@path"))"));
    request.set_uri("http://example.com/foo");
    EXPECT_TRUE(refused(request, R"(sig=("@path"))"));
    request.set_uri("");
    EXPECT_TRUE(refused(request, R"(sig=("@path"))"));
}

TEST(SignatureBaseTest, StatusOfOtherThanThreeDigitsIsRefused) {
    constexpr std::uint16_t four_digits = 1000;
    constexpr std::uint16_t two_digits = 99;
    Response response;

    response.set_status_code(four_digits);
    EXPECT_TRUE(refused(response, R"(sig=("@status"))"));
    response.set_status_code(two_digits);
    EXPECT_TRUE(refused(response, R"(sig=("@status"))"));
}
