#include <gtest/gtest.h>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <string_view>

#include "crypto/ISignKey.hpp"
#include "http/Headers.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "http/StructuredField.hpp"
#include "identity/MessageSigner.hpp"
#include "identity/NodeIdentity.hpp"
#include "identity/SignatureBase.hpp"
#include "storage/tests/Fixtures.hpp"

using crypto::ISignKey;
using http::Headers;
using http::Request;
using http::Response;
using http::StructuredField;
using identity::MessageSigner;
using identity::NodeIdentity;
using identity::SignatureBase;
using std::string;
using std::string_view;
using std::filesystem::perms;

namespace {

// RFC 9421 B.1.4's test-key-ed25519, which made the Python node's
// signatures below.
constexpr string_view rfc_ed25519_private_key =
    "-----BEGIN PRIVATE KEY-----\n"
    "MC4CAQAwBQYDK2VwBCIEIJ+DYvh6SEqVTm50DFtMDoQikTmiCqirVv9mWG9qfSnF\n"
    "-----END PRIVATE KEY-----\n";

// The Python node's signed messages, which its MessageSigner made with
// http-message-signatures 2.0.1, test-key-ed25519, and its clock at
// python_created, run from the top of the repository with
// `PYTHONPATH=python/src uv run python` as:
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
// Each gave back the headers it was given, with the Content-Digest it added
// for the body b"hello", and its Signature-Input and Signature.
constexpr std::int64_t python_created = 1'757'080'000;
constexpr string_view hello_path =
    "/data/sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";
constexpr string_view content_type = "application/octet-stream";

// The time seconds after the UNIX epoch.
MessageSigner::Clock::time_point time_at(std::int64_t seconds) {
    return MessageSigner::Clock::time_point(std::chrono::seconds(seconds));
}

// Signs as identity, with its clock at python_created.
MessageSigner python_signer(const NodeIdentity& identity) {
    return MessageSigner(identity, [] { return time_at(python_created); });
}

// The request the Python node signed first, before it was signed.
Request put_hello() {
    Request request;

    request.set_method("PUT");
    request.set_uri(string(hello_path));
    request.headers().set("Content-Type", string(content_type));
    request.set_body("hello");
    return request;
}

// What headers has under name, or an empty string when it has none.
string field(const Headers& headers, string_view name) {
    return string(headers.get(name).value_or(""));
}

// What the libranet signature's entry in a Signature-Input or Signature
// value names.
StructuredField::Member libranet_member(string_view value) {
    return StructuredField::parse(value).member("libranet").value_or(StructuredField::Item{});
}

// Whether the signature request has is one public_pem's key made of
// request, as its Signature-Input describes it.
bool verifies(const Request& request, string_view public_pem) {
    const auto signature_params = std::get<StructuredField::InnerList>(
        libranet_member(field(request.headers(), "Signature-Input")));
    const auto signature = std::get<StructuredField::Item>(
        libranet_member(field(request.headers(), "Signature")));
    const SignatureBase base(request, signature_params);

    return ISignKey::from_pem(public_pem)
        ->verify(base.to_string(), std::get<StructuredField::Bytes>(signature.value));
}

// The created parameter of the signature request has.
std::int64_t created_of(const Request& request) {
    const auto signature_params = std::get<StructuredField::InnerList>(
        libranet_member(field(request.headers(), "Signature-Input")));

    for (const auto& [name, value] : signature_params.parameters) {
        if ("created" == name) {
            return std::get<std::int64_t>(value);
        }
    }

    return 0;
}

// Each test has a directory for key files, and a store for public keys.
class MessageSignerTest : public testing::Test {
protected:
    // The identity whose key is test-key-ed25519, kept in a key file only its
    // owner may read, as the node keeps its own.
    [[nodiscard]] NodeIdentity rfc_identity() const {
        const auto key_file = m_directory.path() / "rfc-key.pem";

        fixtures::write_file(key_file, rfc_ed25519_private_key);
        std::filesystem::permissions(key_file, perms::owner_read | perms::owner_write);
        return NodeIdentity(key_file, m_store);
    }

    // An identity with a new key, as a node makes the first time it starts.
    [[nodiscard]] NodeIdentity new_identity() const {
        return NodeIdentity(m_directory.path() / "new-key.pem", m_store);
    }

private:
    fixtures::ScratchDirectory m_directory;
    fixtures::MemorySource m_store;
};

} // namespace

TEST_F(MessageSignerTest, PythonRequestWithBody) {
    const auto identity = rfc_identity();
    auto request = put_hello();

    python_signer(identity).sign(request);

    EXPECT_EQ(request.headers().size(), 4U);
    EXPECT_EQ(field(request.headers(), "Content-Type"), content_type);
    EXPECT_EQ(field(request.headers(), "Content-Digest"),
              "sha-256=:LPJNul+wow4m6DsqxbninhsWHlwfp0JecwQzYpOLmCQ=:");
    EXPECT_EQ(field(request.headers(), "Signature-Input"),
              "libranet=(\"@method\" \"@path\" \"content-digest\");created=1757080000"
              ";keyid=\"sha256/22ce02aa18eb1ee5f39482d0f57a6ba56f4d549f81db547f3bea2863207c8a01\"");
    EXPECT_EQ(field(request.headers(), "Signature"),
              "libranet=:qyTBq7bJhfHSzhIeP7HwXGQ4z5CMwmN9bXyFwvYymaB/vh4snxDXzi9CFjO3wdxYzHcN"
              "MoeYgG8OisrCzHxzBw==:");
    EXPECT_EQ(request.body(), "hello");
}

TEST_F(MessageSignerTest, PythonRequestWithoutBody) {
    const auto identity = rfc_identity();
    Request request;

    request.set_method("GET");
    request.set_uri("/data/seek");
    python_signer(identity).sign(request);

    EXPECT_EQ(request.headers().size(), 2U);
    EXPECT_FALSE(request.headers().has("Content-Digest"));
    EXPECT_EQ(field(request.headers(), "Signature-Input"),
              "libranet=(\"@method\" \"@path\");created=1757080000"
              ";keyid=\"sha256/22ce02aa18eb1ee5f39482d0f57a6ba56f4d549f81db547f3bea2863207c8a01\"");
    EXPECT_EQ(field(request.headers(), "Signature"),
              "libranet=:5mf0/toFaLZizh2rfro17a1BtoMCUnSeAodHbuKw5ztru0e/8Sjh9MLT52w1Ow26lO9d"
              "52ec+4575jSoflhfDw==:");
}

TEST_F(MessageSignerTest, PythonAnswerWithBody) {
    const auto identity = rfc_identity();
    Response response;

    response.set_status_code(http::ResponseCode::Status::Ok);
    response.headers().set("Content-Type", string(content_type));
    response.set_body("hello");
    python_signer(identity).sign(response);

    EXPECT_EQ(response.headers().size(), 4U);
    EXPECT_EQ(field(response.headers(), "Content-Type"), content_type);
    EXPECT_EQ(field(response.headers(), "Content-Digest"),
              "sha-256=:LPJNul+wow4m6DsqxbninhsWHlwfp0JecwQzYpOLmCQ=:");
    EXPECT_EQ(field(response.headers(), "Signature-Input"),
              "libranet=(\"@status\" \"content-digest\");created=1757080000"
              ";keyid=\"sha256/22ce02aa18eb1ee5f39482d0f57a6ba56f4d549f81db547f3bea2863207c8a01\"");
    EXPECT_EQ(field(response.headers(), "Signature"),
              "libranet=:1pVYtim0VmBNeA/zYV1Fm3yxuXm0YuiYZx/CRFfcI3x2vJNBeLqohetYGcwRkXnkCPkm"
              "fuzmoc0WBR7wynbBCw==:");
}

TEST_F(MessageSignerTest, PythonAnswerWithoutBody) {
    const auto identity = rfc_identity();
    Response response;

    response.set_status_code(http::ResponseCode::Status::NotFound);
    python_signer(identity).sign(response);

    EXPECT_EQ(response.headers().size(), 2U);
    EXPECT_FALSE(response.headers().has("Content-Digest"));
    EXPECT_EQ(field(response.headers(), "Signature-Input"),
              "libranet=(\"@status\");created=1757080000"
              ";keyid=\"sha256/22ce02aa18eb1ee5f39482d0f57a6ba56f4d549f81db547f3bea2863207c8a01\"");
    EXPECT_EQ(field(response.headers(), "Signature"),
              "libranet=:yB156y/jTYmkK1iUwn2nHvX2DkLZ8lpURdVvLIV0kaBEXbgCTzE1UKywkJeZBs77BI1A"
              "2MXzMtqCIYE7MNfwDQ==:");
}

// The fields of an earlier signature, and the digest of an earlier body, are
// replaced, and part of a second is left out of the time.
TEST_F(MessageSignerTest, SigningAgainReplacesTheSignature) {
    constexpr std::chrono::milliseconds part_of_a_second{999};
    const auto identity = rfc_identity();
    auto now = time_at(1);
    const MessageSigner signer(identity, [&now] { return now; });
    auto request = put_hello();

    request.set_body("an earlier body");
    signer.sign(request);
    request.set_body("hello");
    now = time_at(python_created) + part_of_a_second;
    signer.sign(request);

    EXPECT_EQ(request.headers().size(), 4U);
    EXPECT_EQ(field(request.headers(), "Content-Digest"),
              "sha-256=:LPJNul+wow4m6DsqxbninhsWHlwfp0JecwQzYpOLmCQ=:");
    EXPECT_EQ(field(request.headers(), "Signature-Input"),
              "libranet=(\"@method\" \"@path\" \"content-digest\");created=1757080000"
              ";keyid=\"sha256/22ce02aa18eb1ee5f39482d0f57a6ba56f4d549f81db547f3bea2863207c8a01\"");
    EXPECT_EQ(field(request.headers(), "Signature"),
              "libranet=:qyTBq7bJhfHSzhIeP7HwXGQ4z5CMwmN9bXyFwvYymaB/vh4snxDXzi9CFjO3wdxYzHcN"
              "MoeYgG8OisrCzHxzBw==:");
}

// The signature names the node's id, verifies under its public key, and
// covers the path without its query (RFC 9421 §2.2.6).
TEST_F(MessageSignerTest, SignatureVerifiesUnderTheNodesKey) {
    const auto identity = new_identity();
    const auto other = rfc_identity();
    Request request;

    request.set_method("GET");
    request.set_uri("/data/search?q=hello");
    MessageSigner(identity).sign(request);

    const auto signature_input = field(request.headers(), "Signature-Input");

    EXPECT_EQ(signature_input.rfind("libranet=(\"@method\" \"@path\");created=", 0), 0U);
    EXPECT_NE(signature_input.find(";keyid=\"" + identity.id().to_string() + "\""), string::npos);
    EXPECT_TRUE(verifies(request, identity.public_pem()));
    EXPECT_FALSE(verifies(request, other.public_pem()));
    request.set_uri("/data/search?q=other");
    EXPECT_TRUE(verifies(request, identity.public_pem()));
    request.set_uri("/data/seek");
    EXPECT_FALSE(verifies(request, identity.public_pem()));
}

TEST_F(MessageSignerTest, SignsAtTheTimeOfTheSystemClock) {
    const auto identity = rfc_identity();
    Request request;

    request.set_method("GET");
    request.set_uri("/data/seek");

    const auto before = std::chrono::duration_cast<std::chrono::seconds>(
        MessageSigner::Clock::now().time_since_epoch());

    MessageSigner(identity).sign(request);

    const auto after = std::chrono::duration_cast<std::chrono::seconds>(
        MessageSigner::Clock::now().time_since_epoch());

    EXPECT_GE(created_of(request), before.count());
    EXPECT_LE(created_of(request), after.count());
}

TEST_F(MessageSignerTest, NoClockIsRefused) {
    const auto identity = rfc_identity();

    EXPECT_THROW((void)MessageSigner(identity, nullptr), std::invalid_argument);
}

TEST_F(MessageSignerTest, UnsignableMessageIsRefused) {
    constexpr std::uint16_t four_digits = 1000;
    const auto identity = rfc_identity();
    const auto signer = python_signer(identity);
    Request request;
    Response response;

    request.set_method("OPTIONS");
    request.set_uri("*");
    response.set_status_code(four_digits);

    EXPECT_THROW(signer.sign(request), std::invalid_argument);
    EXPECT_THROW(signer.sign(response), std::invalid_argument);
}
