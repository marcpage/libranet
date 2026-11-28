#include <gtest/gtest.h>
#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdint>
#include <map>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "crypto/Base64.hpp"
#include "crypto/ISignKey.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "http/StructuredField.hpp"
#include "identity/ContentDigest.hpp"
#include "identity/KnownKeys.hpp"
#include "identity/MessageVerifier.hpp"
#include "identity/SignatureBase.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Converter.hpp"

using crypto::Base64;
using crypto::ISignKey;
using http::Request;
using http::Response;
using http::StructuredField;
using identity::ContentDigest;
using identity::KnownKeys;
using identity::MessageVerifier;
using identity::SignatureBase;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;

namespace {

// When the Python node made the signatures below, and when the node's clock
// stands in these tests.
constexpr std::int64_t now_seconds = 1'757'080'000;

// RFC 9421 B.1.4's test-key-ed25519, which made the Python node's
// signatures below, and its id: the SHA-256 of its PEM, as
// `shasum -a 256` gives it.
constexpr std::string_view rfc_ed25519_public_key =
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEAJrQLj5P/89iXES9+vFgrIy29clF9CC/oPPsw3c5D0bs=\n"
    "-----END PUBLIC KEY-----\n";
constexpr std::string_view python_keyid =
    "sha256/22ce02aa18eb1ee5f39482d0f57a6ba56f4d549f81db547f3bea2863207c8a01";

// The Python node's signatures, which its MessageSigner made with
// http-message-signatures 2.0.1, test-key-ed25519, and its clock at
// now_seconds, run with PYTHONPATH=python/src as:
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
// The Content-Digest is the one it added for the body b"hello".
constexpr std::string_view python_digest = "sha-256=:LPJNul+wow4m6DsqxbninhsWHlwfp0JecwQzYpOLmCQ=:";
constexpr std::string_view hello_path =
    "/data/sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";
constexpr std::string_view python_put_signature =
    "qyTBq7bJhfHSzhIeP7HwXGQ4z5CMwmN9bXyFwvYymaB/vh4snxDXzi9CFjO3wdxYzHcNMoeYgG8OisrCzHxzBw==";
constexpr std::string_view python_get_signature =
    "5mf0/toFaLZizh2rfro17a1BtoMCUnSeAodHbuKw5ztru0e/8Sjh9MLT52w1Ow26lO9d52ec+4575jSoflhfDw==";
constexpr std::string_view python_ok_signature =
    "1pVYtim0VmBNeA/zYV1Fm3yxuXm0YuiYZx/CRFfcI3x2vJNBeLqohetYGcwRkXnkCPkmfuzmoc0WBR7wynbBCw==";
constexpr std::string_view python_not_found_signature =
    "yB156y/jTYmkK1iUwn2nHvX2DkLZ8lpURdVvLIV0kaBEXbgCTzE1UKywkJeZBs77BI1A2MXzMtqCIYE7MNfwDQ==";

// The components a node's signature on a request covers, and on an answer,
// without a body and with one (HTTP API §11).
constexpr std::string_view request_components = R"("@method" "@path")";
constexpr std::string_view request_components_with_body =
    R"("@method" "@path" "content-digest")";
constexpr std::string_view answer_components = R"("@status")";
constexpr std::string_view answer_components_with_body = R"("@status" "content-digest")";

// A store that keeps what it is given in memory, by identifier. put() is
// const in IDataSource, so what it keeps is mutable.
class MemorySource : public IDataSource {
public:
    [[nodiscard]] bool has(const Identifier& identifier) const override {
        return m_data.find(identifier.to_string()) != m_data.end();
    }

    [[nodiscard]] std::optional<Data> get(const Identifier& identifier) const override {
        const auto found = m_data.find(identifier.to_string());

        if (found == m_data.end()) {
            return std::nullopt;
        }

        return found->second;
    }

    void put(const Identifier& identifier, const Data& body) const override {
        m_data[identifier.to_string()] = body;
    }

    [[nodiscard]] bool remove(const Identifier& identifier) const override {
        return m_data.erase(identifier.to_string()) > 0;
    }

    [[nodiscard]] std::uintmax_t size() const override {
        return m_data.size();
    }

    [[nodiscard]] Identifiers starting_with(
        [[maybe_unused]] const storage::HashPrefix& prefix) const override {
        return {};
    }

private:
    mutable std::map<std::string, Data> m_data;
};

// Stores text in store as content, under its identifier, which comes back.
Identifier store_content(const IDataSource& store, std::string_view text) {
    const auto content = Converter::bytes_of(text);
    const auto node_id = Identifier::of(content);

    store.put(node_id, content);
    return node_id;
}

// A clock that stands at seconds after the UNIX epoch.
MessageVerifier::ClockFunction clock_at(std::int64_t seconds) {
    return [seconds] {
        return MessageVerifier::Clock::time_point(std::chrono::seconds(seconds));
    };
}

// A verifier of the keys store holds, with its clock at now_seconds.
MessageVerifier verifier_of(const IDataSource::Ptr& store,
                            const MessageVerifier::Window& window = {}) {
    return {std::make_shared<KnownKeys>(store), window, clock_at(now_seconds)};
}

// A node, whose Ed25519 key is new, and whose public key is in a store.
class TestNode {
public:
    explicit TestNode(const IDataSource& store)
        : m_key(ISignKey::Ed25519())
        , m_id(store_content(store, m_key->public_pem())) {}

    [[nodiscard]] const Identifier& id() const {
        return m_id;
    }

    // The Signature-Input value, less its label, of this node's signature
    // covering components, made at created, with more parameters after.
    [[nodiscard]] std::string params(std::string_view components,
                                     std::int64_t created = now_seconds,
                                     std::string_view more = "") const {
        return "(" + std::string(components) + ");created=" + std::to_string(created)
               + ";keyid=\"" + m_id.to_string() + "\"" + std::string(more);
    }

    // message, with a libranet signature made with this node's key, whose
    // Signature-Input value, less its label, is signature_params.
    template<typename Message>
    [[nodiscard]] Message sign(Message message, const std::string& signature_params) const {
        const auto field = StructuredField::parse("libranet=" + signature_params);
        const auto& inner_list = std::get<StructuredField::InnerList>(field.members().at(0).second);
        const SignatureBase base(message, inner_list);

        message.headers().set("Signature-Input", "libranet=" + signature_params);
        message.headers().set("Signature",
                              "libranet=:" + Base64::encode(m_key->sign(base.to_string())) + ":");
        return message;
    }

private:
    ISignKey::Ptr m_key;
    Identifier m_id;
};

Request request_of(std::string method, std::string uri) {
    Request request;

    request.set_method(std::move(method));
    request.set_uri(std::move(uri));
    return request;
}

// A request that uploads body, with its Content-Digest.
Request upload_of(std::string_view body) {
    auto request = request_of("PUT", std::string(hello_path));

    request.headers().set("Content-Digest", ContentDigest::of(body).to_string());
    request.set_body(std::string(body));
    return request;
}

// An answer of status, with body and its Content-Digest when there is one.
Response answer_of(http::ResponseCode::Status status, std::string_view body = "") {
    Response response;

    response.set_status_code(status);

    if (!body.empty()) {
        response.headers().set("Content-Digest", ContentDigest::of(body).to_string());
        response.set_body(std::string(body));
    }

    return response;
}

// message, with the Python node's signature that covers components.
template<typename Message>
Message python_signed(Message message, std::string_view components, std::string_view signature) {
    message.headers().set("Signature-Input", "libranet=(" + std::string(components)
                                                 + ");created=" + std::to_string(now_seconds)
                                                 + ";keyid=\"" + std::string(python_keyid)
                                                 + "\"");
    message.headers().set("Signature", "libranet=:" + std::string(signature) + ":");
    return message;
}

// The Python node's upload of b"hello", with its Content-Type and
// Content-Digest.
Request python_put() {
    auto request = request_of("PUT", std::string(hello_path));

    request.headers().set("Content-Type", "application/octet-stream");
    request.headers().set("Content-Digest", std::string(python_digest));
    request.set_body("hello");
    return python_signed(request, request_components_with_body, python_put_signature);
}

Request python_get() {
    return python_signed(request_of("GET", "/data/seek"), request_components,
                         python_get_signature);
}

// The Python node's answer of b"hello", with its Content-Type and
// Content-Digest.
Response python_ok() {
    Response response;

    response.set_status_code(http::ResponseCode::Status::Ok);
    response.headers().set("Content-Type", "application/octet-stream");
    response.headers().set("Content-Digest", std::string(python_digest));
    response.set_body("hello");
    return python_signed(response, answer_components_with_body, python_ok_signature);
}

Response python_not_found() {
    return python_signed(answer_of(http::ResponseCode::Status::NotFound), answer_components,
                         python_not_found_signature);
}

// message, with its header name set to value.
template<typename Message>
Message with_header(Message message, std::string name, std::string value) {
    message.headers().set(std::move(name), std::move(value));
    return message;
}

// Whether verifier turns down the signature on message as failing.
template<typename Message>
bool refused(const MessageVerifier& verifier, const Message& message) {
    try {
        (void)verifier.verify(message);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

// Whether verifier finds that the signature on message was made by node_id's
// key.
template<typename Message>
bool verified_as(const MessageVerifier& verifier, const Message& message,
                 const Identifier& node_id) {
    const auto signer = verifier.verify(message);

    return signer && signer->verified && signer->node_id.to_string() == node_id.to_string();
}

} // namespace

// The tests read optionals after ASSERT_TRUE(x.has_value()), which this
// check does not follow.
// NOLINTBEGIN(bugprone-unchecked-optional-access)

TEST(MessageVerifierTest, PythonRequestsAreVerified) {
    const auto store = std::make_shared<MemorySource>();
    const auto node_id = store_content(*store, rfc_ed25519_public_key);
    const auto verifier = verifier_of(store);

    ASSERT_EQ(node_id.to_string(), python_keyid);
    EXPECT_TRUE(verified_as(verifier, python_put(), node_id));
    EXPECT_TRUE(verified_as(verifier, python_get(), node_id));
}

TEST(MessageVerifierTest, PythonAnswersAreVerified) {
    const auto store = std::make_shared<MemorySource>();
    const auto node_id = store_content(*store, rfc_ed25519_public_key);
    const auto verifier = verifier_of(store);

    EXPECT_TRUE(verified_as(verifier, python_ok(), node_id));
    EXPECT_TRUE(verified_as(verifier, python_not_found(), node_id));
}

TEST(MessageVerifierTest, UnsignedMessageIsNotSigned) {
    const auto verifier = verifier_of(std::make_shared<MemorySource>());

    EXPECT_FALSE(verifier.verify(request_of("GET", "/data/seek")).has_value());
    EXPECT_FALSE(verifier.verify(upload_of("hello")).has_value());
    EXPECT_FALSE(verifier.verify(answer_of(http::ResponseCode::Status::Ok, "hello")).has_value());
}

// HandshakeProtocol §2: a signer whose key is not held yet is told from a
// signature that fails.
TEST(MessageVerifierTest, SignerWhoseKeyIsNotHeldIsNamed) {
    const auto verifier = verifier_of(std::make_shared<MemorySource>());

    const auto request_signer = verifier.verify(python_put());
    const auto answer_signer = verifier.verify(python_not_found());

    ASSERT_TRUE(request_signer.has_value());
    ASSERT_TRUE(answer_signer.has_value());
    EXPECT_EQ(request_signer->node_id.to_string(), python_keyid);
    EXPECT_FALSE(request_signer->verified);
    EXPECT_EQ(answer_signer->node_id.to_string(), python_keyid);
    EXPECT_FALSE(answer_signer->verified);
}

// Only the signature itself needs the key, and all else is checked without
// it.
TEST(MessageVerifierTest, SignerWhoseKeyIsNotHeldIsStillChecked) {
    const auto verifier = verifier_of(std::make_shared<MemorySource>());
    auto changed_body = python_put();

    changed_body.set_body("jello");

    EXPECT_TRUE(refused(verifier, changed_body));
    EXPECT_TRUE(refused(verifier, with_header(python_get(), "Signature-Input",
                                              R"(libranet=("@method");created=1757080000)"
                                              R"(;keyid="sha256/22ce02aa18eb1ee5f39482d0f57a6)"
                                              R"(ba56f4d549f81db547f3bea2863207c8a01")")));
    EXPECT_FALSE(refused(verifier, with_header(python_get(), "Signature",
                                               "libranet=:" + std::string(python_ok_signature)
                                                   + ":")));
}

TEST(MessageVerifierTest, SignatureTheKeyDidNotMakeIsRefused) {
    const auto store = std::make_shared<MemorySource>();
    (void)store_content(*store, rfc_ed25519_public_key);
    const auto verifier = verifier_of(store);

    EXPECT_TRUE(refused(verifier, with_header(python_get(), "Signature",
                                              "libranet=:" + std::string(python_ok_signature)
                                                  + ":")));
    EXPECT_TRUE(refused(verifier, with_header(python_get(), "Signature", "libranet=:AAAA:")));
    EXPECT_TRUE(refused(verifier, python_signed(request_of("GET", "/data/nodes"),
                                                request_components, python_get_signature)));
    EXPECT_TRUE(refused(verifier, python_signed(request_of("HEAD", "/data/seek"),
                                                request_components, python_get_signature)));
    EXPECT_TRUE(refused(verifier, python_signed(answer_of(http::ResponseCode::Status::Gone),
                                                answer_components, python_not_found_signature)));
}

// A node cannot sign as another, whose key the store holds.
TEST(MessageVerifierTest, SignatureNamingAnotherNodeIsRefused) {
    const auto store = std::make_shared<MemorySource>();
    const TestNode signer(*store);
    const TestNode other(*store);
    const auto verifier = verifier_of(store);
    const auto request = request_of("GET", "/data/seek");

    EXPECT_TRUE(verified_as(verifier, signer.sign(request, signer.params(request_components)),
                            signer.id()));
    EXPECT_TRUE(refused(verifier, signer.sign(request, other.params(request_components))));
}

// KnownKeys refuses what the store holds as the key.
TEST(MessageVerifierTest, SignerWhoseKeyIsNotOneIsRefused) {
    const auto store = std::make_shared<MemorySource>();
    const TestNode signer(*store);
    const auto not_a_key = store_content(*store, "Not a key");
    const auto verifier = verifier_of(store);

    EXPECT_TRUE(refused(verifier, signer.sign(request_of("GET", "/data/seek"),
                                              R"(("@method" "@path");created=1757080000)"
                                              R"(;keyid=")"
                                                  + not_a_key.to_string() + "\"")));
}

TEST(MessageVerifierTest, OneSignatureHeaderWithoutTheOtherIsRefused) {
    const auto verifier = verifier_of(std::make_shared<MemorySource>());
    auto without_input = python_get();
    auto without_signature = python_get();

    without_input.headers().remove("Signature-Input");
    without_signature.headers().remove("Signature");

    EXPECT_TRUE(refused(verifier, without_input));
    EXPECT_TRUE(refused(verifier, without_signature));
}

// HTTP API §11.2: a message carries exactly one signature, labelled
// libranet.
TEST(MessageVerifierTest, OtherThanOneLibranetSignatureIsRefused) {
    const auto verifier = verifier_of(std::make_shared<MemorySource>());
    const auto input = std::string(python_get().headers().get("Signature-Input", ""));
    const auto signature = std::string(python_get().headers().get("Signature", ""));
    const auto relabelled = [](std::string value) {
        return value.replace(0, std::string_view("libranet").size(), "sig1");
    };

    EXPECT_TRUE(refused(verifier, with_header(with_header(python_get(), "Signature-Input",
                                                          relabelled(input)),
                                              "Signature", relabelled(signature))));
    EXPECT_TRUE(refused(verifier, with_header(python_get(), "Signature-Input",
                                              input + ", " + relabelled(input))));
    EXPECT_TRUE(refused(verifier, with_header(python_get(), "Signature",
                                              signature + ", " + relabelled(signature))));
}

TEST(MessageVerifierTest, SignatureHeadersNotAsRfc9421GivesThemAreRefused) {
    const auto verifier = verifier_of(std::make_shared<MemorySource>());

    EXPECT_TRUE(refused(verifier, with_header(python_get(), "Signature-Input", "libranet=((")));
    EXPECT_TRUE(refused(verifier, with_header(python_get(), "Signature-Input",
                                              R"(libranet="@method")")));
    EXPECT_TRUE(refused(verifier, with_header(python_get(), "Signature", "libranet=:@@:")));
    EXPECT_TRUE(refused(verifier, with_header(python_get(), "Signature", R"(libranet="AAAA")")));
    EXPECT_TRUE(refused(verifier, with_header(python_get(), "Signature", "libranet=(:AAAA:)")));
}

// HTTP API §11: a request's signature covers @method and @path, and
// content-digest when there is a body.
TEST(MessageVerifierTest, RequestSignatureCoveringTooLittleIsRefused) {
    const auto store = std::make_shared<MemorySource>();
    const TestNode node(*store);
    const auto verifier = verifier_of(store);
    const auto get = request_of("GET", "/data/seek");
    const auto upload = upload_of("hello");

    EXPECT_TRUE(refused(verifier, node.sign(get, node.params(R"("@method")"))));
    EXPECT_TRUE(refused(verifier, node.sign(get, node.params(R"("@path")"))));
    EXPECT_TRUE(refused(verifier, python_signed(get, answer_components,
                                                python_not_found_signature)));
    EXPECT_TRUE(refused(verifier, node.sign(upload, node.params(request_components))));
    EXPECT_TRUE(verified_as(verifier, node.sign(upload, node.params(request_components_with_body)),
                            node.id()));
}

// HTTP API §11: an answer's signature covers @status, and content-digest
// when there is a body.
TEST(MessageVerifierTest, AnswerSignatureCoveringTooLittleIsRefused) {
    const auto store = std::make_shared<MemorySource>();
    const TestNode node(*store);
    const auto verifier = verifier_of(store);
    const auto empty = answer_of(http::ResponseCode::Status::NoContent);
    const auto full = answer_of(http::ResponseCode::Status::Ok, "hello");

    EXPECT_TRUE(refused(verifier, node.sign(empty, node.params(""))));
    EXPECT_TRUE(refused(verifier, node.sign(full, node.params(answer_components))));
    EXPECT_TRUE(verified_as(verifier, node.sign(full, node.params(answer_components_with_body)),
                            node.id()));
}

// A Content-Digest sent without a body says the body is empty, and is
// covered too.
TEST(MessageVerifierTest, DigestOfNoBodyMustBeCovered) {
    const auto store = std::make_shared<MemorySource>();
    const TestNode node(*store);
    const auto verifier = verifier_of(store);
    const auto answer = with_header(answer_of(http::ResponseCode::Status::NoContent),
                                    "Content-Digest", ContentDigest::of("").to_string());

    EXPECT_TRUE(refused(verifier, node.sign(answer, node.params(answer_components))));
    EXPECT_TRUE(verified_as(verifier, node.sign(answer, node.params(answer_components_with_body)),
                            node.id()));
}

// A signature may cover more than it must, as RFC 9421's own examples do.
TEST(MessageVerifierTest, SignatureCoveringMoreIsVerified) {
    const auto store = std::make_shared<MemorySource>();
    const TestNode node(*store);
    const auto verifier = verifier_of(store);
    const auto request = with_header(request_of("GET", "/data/seek"), "Host", "example.com");

    EXPECT_TRUE(verified_as(verifier,
                            node.sign(request, node.params(R"("@authority" "@path" "@method")")),
                            node.id()));
}

// HTTP API §11.2: the digest a signature covers is the body's, by an
// algorithm the node knows.
TEST(MessageVerifierTest, DigestThatIsNotTheBodysIsRefused) {
    const auto store = std::make_shared<MemorySource>();
    const TestNode node(*store);
    const auto verifier = verifier_of(store);
    auto changed_body = node.sign(upload_of("hello"), node.params(request_components_with_body));
    auto unknown_digest = with_header(upload_of("hello"), "Content-Digest",
                                      "md5=:XUFAKrxLKna5cZ2REBfFkg==:");

    changed_body.set_body("jello");
    unknown_digest = node.sign(unknown_digest, node.params(request_components_with_body));

    EXPECT_TRUE(refused(verifier, changed_body));
    EXPECT_TRUE(refused(verifier, unknown_digest));
}

TEST(MessageVerifierTest, DigestTheMessageDoesNotHaveIsRefused) {
    const auto verifier = verifier_of(std::make_shared<MemorySource>());
    const auto request = with_header(python_get(), "Signature-Input",
                                     R"(libranet=("@method" "@path" "content-digest"))"
                                     R"(;created=1757080000;keyid="sha256/22ce02aa18eb1ee5f39482d)"
                                     R"(0f57a6ba56f4d549f81db547f3bea2863207c8a01")");

    EXPECT_TRUE(refused(verifier, request));
}

// HTTP API §11.2: keyid is the signer's node id, given as a string.
TEST(MessageVerifierTest, KeyidThatIsNotAStringIsRefused) {
    const auto store = std::make_shared<MemorySource>();
    const TestNode node(*store);
    const auto verifier = verifier_of(store);
    const auto request = request_of("GET", "/data/seek");
    const std::string signature_params = R"(("@method" "@path");created=1757080000)";

    EXPECT_TRUE(refused(verifier, node.sign(request, signature_params)));
    EXPECT_TRUE(refused(verifier, node.sign(request, signature_params + ";keyid=sha256")));
    EXPECT_TRUE(refused(verifier, node.sign(request, signature_params + ";keyid=1")));
}

// HTTP API §11.2: keyid is the signer's node id, and nothing more.
TEST(MessageVerifierTest, KeyidThatIsNotANodeIdIsRefused) {
    const auto store = std::make_shared<MemorySource>();
    const TestNode node(*store);
    const auto verifier = verifier_of(store);
    const auto request = request_of("GET", "/data/seek");
    const auto node_id = node.id().to_string();
    const auto signed_as = [&](const std::string& keyid) {
        return node.sign(request, R"(("@method" "@path");created=1757080000;keyid=")" + keyid
                                      + "\"");
    };

    EXPECT_TRUE(refused(verifier, signed_as("sha256/2cf24dba")));
    EXPECT_TRUE(refused(verifier, signed_as("/" + node_id)));
    EXPECT_TRUE(refused(verifier, signed_as(node_id + "/")));
    EXPECT_TRUE(refused(verifier, signed_as("sha512/" + node.id().hash())));
    EXPECT_TRUE(verified_as(verifier, signed_as(node_id), node.id()));
}

// HTTP API §5.4: the hash of an id may be in any case.
TEST(MessageVerifierTest, KeyidInUpperCaseIsVerified) {
    const auto store = std::make_shared<MemorySource>();
    const TestNode node(*store);
    const auto verifier = verifier_of(store);
    auto upper_case_hash = node.id().hash();

    std::transform(upper_case_hash.begin(), upper_case_hash.end(), upper_case_hash.begin(),
                   [](char digit) {
        return static_cast<char>(std::toupper(static_cast<unsigned char>(digit)));
    });

    EXPECT_TRUE(verified_as(verifier,
                            node.sign(request_of("GET", "/data/seek"),
                                      R"(("@method" "@path");created=1757080000;keyid="sha256/)"
                                          + upper_case_hash + "\""),
                            node.id()));
}

TEST(MessageVerifierTest, CreatedTimeMustBeGiven) {
    const auto store = std::make_shared<MemorySource>();
    const TestNode node(*store);
    const auto verifier = verifier_of(store);
    const auto request = request_of("GET", "/data/seek");
    const auto keyid = ";keyid=\"" + node.id().to_string() + "\"";

    EXPECT_TRUE(refused(verifier, node.sign(request, R"(("@method" "@path"))" + keyid)));
    EXPECT_TRUE(refused(verifier,
                        node.sign(request, R"(("@method" "@path");created="1757080000")" + keyid)));
    EXPECT_TRUE(refused(verifier,
                        node.sign(request, node.params(request_components, now_seconds,
                                                       ";expires=\"1757080000\""))));
}

// HandshakeProtocol §2: no more than 5 seconds before the node's clock, nor
// ahead of it, each widened by 30 seconds' skew, by default.
TEST(MessageVerifierTest, CreatedTimeWithinTheDefaultWindowIsVerified) {
    constexpr std::int64_t oldest = now_seconds - 35;
    constexpr std::int64_t newest = now_seconds + 30;
    const auto store = std::make_shared<MemorySource>();
    const TestNode node(*store);
    const auto verifier = verifier_of(store);
    const auto request = request_of("GET", "/data/seek");

    EXPECT_TRUE(verified_as(verifier, node.sign(request, node.params(request_components, oldest)),
                            node.id()));
    EXPECT_TRUE(verified_as(verifier, node.sign(request, node.params(request_components, newest)),
                            node.id()));
    EXPECT_TRUE(refused(verifier, node.sign(request, node.params(request_components, oldest - 1))));
    EXPECT_TRUE(refused(verifier, node.sign(request, node.params(request_components, newest + 1))));
}

TEST(MessageVerifierTest, CreatedTimeWithinAWindowSetIsVerified) {
    constexpr std::int64_t max_age_seconds = 10;
    const auto store = std::make_shared<MemorySource>();
    const TestNode node(*store);
    const auto verifier = verifier_of(
        store, {std::chrono::seconds(max_age_seconds), MessageVerifier::Milliseconds::zero()});
    const auto request = request_of("GET", "/data/seek");
    const auto signed_at = [&](std::int64_t created) {
        return node.sign(request, node.params(request_components, created));
    };

    EXPECT_TRUE(verified_as(verifier, signed_at(now_seconds - max_age_seconds), node.id()));
    EXPECT_TRUE(verified_as(verifier, signed_at(now_seconds), node.id()));
    EXPECT_TRUE(refused(verifier, signed_at(now_seconds - max_age_seconds - 1)));
    EXPECT_TRUE(refused(verifier, signed_at(now_seconds + 1)));
}

// HandshakeProtocol §2: an expires time is allowed the same skew.
TEST(MessageVerifierTest, ExpiredSignatureIsRefused) {
    constexpr std::int64_t skew_seconds = 30;
    const auto store = std::make_shared<MemorySource>();
    const TestNode node(*store);
    const auto verifier = verifier_of(store);
    const auto request = request_of("GET", "/data/seek");
    const auto expiring_at = [&](std::int64_t expires) {
        return node.sign(request, node.params(request_components, now_seconds,
                                              ";expires=" + std::to_string(expires)));
    };

    EXPECT_TRUE(verified_as(verifier, expiring_at(now_seconds + skew_seconds), node.id()));
    EXPECT_TRUE(verified_as(verifier, expiring_at(now_seconds - skew_seconds), node.id()));
    EXPECT_TRUE(refused(verifier, expiring_at(now_seconds - skew_seconds - 1)));
}

// A structured field's integer may be as large as 15 digits, far more
// seconds than a clock's time can hold.
TEST(MessageVerifierTest, TimesFarFromNowAreRefused) {
    constexpr std::int64_t largest = 999'999'999'999'999;
    const auto store = std::make_shared<MemorySource>();
    const TestNode node(*store);
    const auto verifier = verifier_of(store);
    const auto request = request_of("GET", "/data/seek");

    EXPECT_TRUE(refused(verifier, node.sign(request, node.params(request_components, largest))));
    EXPECT_TRUE(refused(verifier, node.sign(request, node.params(request_components, -largest))));
    EXPECT_TRUE(refused(verifier,
                        node.sign(request, node.params(request_components, now_seconds,
                                                       ";expires=" + std::to_string(-largest)))));
    EXPECT_TRUE(verified_as(verifier,
                            node.sign(request, node.params(request_components, now_seconds,
                                                           ";expires=" + std::to_string(largest))),
                            node.id()));
}

// RFC 9421 §3.2: an alg parameter must name the key's algorithm.
TEST(MessageVerifierTest, AlgorithmOtherThanEd25519IsRefused) {
    const auto store = std::make_shared<MemorySource>();
    const TestNode node(*store);
    const auto verifier = verifier_of(store);
    const auto request = request_of("GET", "/data/seek");
    const auto with_alg = [&](std::string_view alg) {
        return node.sign(request, node.params(request_components, now_seconds,
                                              ";alg=" + std::string(alg)));
    };

    EXPECT_TRUE(verified_as(verifier, with_alg(R"("ed25519")"), node.id()));
    EXPECT_TRUE(refused(verifier, with_alg(R"("rsa-pss-sha512")")));
    EXPECT_TRUE(refused(verifier, with_alg("ed25519")));
}

TEST(MessageVerifierTest, WhatItCannotWorkWithIsRefused) {
    const auto keys = std::make_shared<KnownKeys>(std::make_shared<MemorySource>());
    const auto max_age = MessageVerifier::default_max_age;
    const auto clock_skew = MessageVerifier::default_clock_skew;
    const auto zero = MessageVerifier::Milliseconds::zero();
    const auto less_than_zero = MessageVerifier::Milliseconds(-1);

    EXPECT_THROW((void)MessageVerifier(nullptr, {}), std::invalid_argument);
    EXPECT_THROW((void)MessageVerifier(keys, {zero, clock_skew}), std::invalid_argument);
    EXPECT_THROW((void)MessageVerifier(keys, {max_age, less_than_zero}), std::invalid_argument);
    EXPECT_THROW((void)MessageVerifier(keys, {}, MessageVerifier::ClockFunction()),
                 std::invalid_argument);
    EXPECT_NO_THROW((void)MessageVerifier(keys, {max_age, zero}));
}

// NOLINTEND(bugprone-unchecked-optional-access)
