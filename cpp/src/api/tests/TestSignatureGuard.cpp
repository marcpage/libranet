#include <gtest/gtest.h>
#include <cstddef>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "api/SignatureGuard.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "identity/KnownKeys.hpp"
#include "identity/MessageSigner.hpp"
#include "identity/MessageVerifier.hpp"
#include "identity/NodeIdentity.hpp"
#include "net/Address.hpp"
#include "storage/Identifier.hpp"
#include "storage/tests/Fixtures.hpp"
#include "types/Json.hpp"
#include "web/ConnectionInfo.hpp"

using api::SignatureGuard;
using http::Request;
using http::Response;
using identity::KnownKeys;
using identity::MessageSigner;
using identity::MessageVerifier;
using identity::NodeIdentity;
using net::Address;
using types::Json;
using web::ConnectionInfo;

namespace {

using Handler = SignatureGuard::Handler;
using Status = http::ResponseCode::Status;
using Statuses = std::vector<int>;

// The path of content, and what the handler answers each request it serves
// with, and that answer's Content-Digest.
constexpr std::string_view hello_path =
    "/data/sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";
constexpr std::string_view served = "served";
constexpr std::string_view served_digest = "sha-256=:j+iL8BW1Oze1+oTccmzZOqzKl/YBGFvhH1+3T6PXeAI=:";

// The problem types the guard answers with (HTTP API §17.2).
constexpr std::string_view signature_required =
    "https://libranet.org/problems/signature-required";
constexpr std::string_view invalid_signature = "https://libranet.org/problems/invalid-signature";

// Where a connection comes from: an address set aside for documentation
// (RFC 5737), and a port the system might pick for a client.
constexpr std::string_view remote_host = "192.0.2.1";
constexpr Address::Port remote_port = 50000;

ConnectionInfo new_connection() {
    return ConnectionInfo(Address(remote_host, remote_port));
}

Request request_of(std::string method, std::string_view uri = hello_path,
                   std::string_view body = "") {
    Request request;

    request.set_method(std::move(method));
    request.set_uri(std::string(uri));
    request.set_body(std::string(body));
    return request;
}

// request, signed by peer.
Request signed_by(const NodeIdentity& peer, Request request) {
    MessageSigner(peer).sign(request);
    return request;
}

// The answer guarded makes to request, which came on connection.
Response answer(const Handler& guarded, const Request& request, ConnectionInfo& connection) {
    Response response;

    guarded(request, response, connection);
    return response;
}

// The status of the answer to each GET signed by a peer of peers, sent in
// turn on connection.
Statuses statuses(const Handler& guarded, const std::vector<const NodeIdentity*>& peers,
                  ConnectionInfo& connection) {
    Statuses found;

    for (const auto* const peer : peers) {
        found.push_back(answer(guarded, signed_by(*peer, request_of("GET")), connection)
                            .status_code());
    }

    return found;
}

// The node the connection carries the signature of, or an empty string.
std::string node_of(const ConnectionInfo& connection) {
    const auto& node = connection.node();

    return node ? node->to_string() : "";
}

// The type of the problem a response describes.
std::string problem_type(const Response& response) {
    return Json::parse(response.body()).at("type").string();
}

// Each test has a node, whose key its store holds, guarding a handler that
// counts what it serves, and peers whose keys the store holds only once a
// test puts them there.
class SignatureGuardTest : public testing::Test {
protected:
    // The guard of this test's node, checking with the keys its store holds.
    [[nodiscard]] std::unique_ptr<SignatureGuard>
    new_guard(std::size_t attempts = SignatureGuard::default_provisional_trust_attempts,
              std::size_t tracked_signers = SignatureGuard::default_tracked_signers) const {
        return std::make_unique<SignatureGuard>(
            MessageVerifier(std::make_shared<KnownKeys>(m_store), MessageVerifier::Window{}),
            MessageSigner(m_identity), attempts, tracked_signers);
    }

    // A handler that answers 200 with served, counting the requests it
    // serves.
    [[nodiscard]] Handler handler() {
        return [this](const Request& /*request*/, Response& response,
                      ConnectionInfo& /*connection*/) {
            ++m_served;
            response.set_status_code(Status::Ok);
            response.set_body(std::string(served));
        };
    }

    // A peer, whose key this node's store does not hold.
    [[nodiscard]] NodeIdentity new_peer(std::string_view name) const {
        return NodeIdentity(m_directory.path() / (std::string(name) + "-key.pem"), m_peer_store);
    }

    // Has this node's store hold peer's key, as once it was put there.
    void obtain_key(const NodeIdentity& peer) const {
        m_store->put(peer.id(), fixtures::bytes_of(peer.public_pem()));
    }

    // Whether this node signed response, and the signature verifies.
    [[nodiscard]] bool is_signed_by_node(const Response& response) const {
        const auto signer =
            MessageVerifier(std::make_shared<KnownKeys>(m_store), MessageVerifier::Window{})
                .verify(response);

        return signer && signer->verified
               && signer->node_id.to_string() == m_identity.id().to_string();
    }

    [[nodiscard]] std::size_t served_count() const noexcept {
        return m_served;
    }

private:
    fixtures::ScratchDirectory m_directory;
    std::shared_ptr<fixtures::MemorySource> m_store = std::make_shared<fixtures::MemorySource>();
    fixtures::MemorySource m_peer_store;
    NodeIdentity m_identity{m_directory.path() / "node-key.pem", *m_store};
    std::size_t m_served = 0;
};

} // namespace

TEST_F(SignatureGuardTest, UnsignedReadsAreServed) {
    const auto guard = new_guard();
    const auto guarded = guard->in_front_of(handler());
    auto connection = new_connection();
    const auto response = answer(guarded, request_of("GET"), connection);

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.body(), served);
    EXPECT_FALSE(connection.node().has_value());
    EXPECT_FALSE(connection.is_closing());
    EXPECT_EQ(answer(guarded, request_of("HEAD"), connection).status_code(), 200);
    EXPECT_EQ(answer(guarded, request_of("DELETE"), connection).status_code(), 200);
    EXPECT_EQ(served_count(), 3U);
}

TEST_F(SignatureGuardTest, UnsignedWritesAreRefused) {
    const auto guard = new_guard();
    const auto guarded = guard->in_front_of(handler());
    auto connection = new_connection();
    const auto put = answer(guarded, request_of("PUT", hello_path, "hello"), connection);
    const auto post = answer(guarded, request_of("POST", "/data/nodes", "{}"), connection);

    EXPECT_EQ(put.status_code(), 401);
    EXPECT_EQ(put.headers().get("Content-Type"), "application/problem+json");
    EXPECT_EQ(problem_type(put), signature_required);
    EXPECT_TRUE(is_signed_by_node(put));
    EXPECT_EQ(post.status_code(), 401);
    EXPECT_EQ(problem_type(post), signature_required);
    EXPECT_FALSE(connection.is_closing());
    EXPECT_EQ(served_count(), 0U);
}

TEST_F(SignatureGuardTest, EachAnswerIsSigned) {
    const auto guard = new_guard();
    const auto guarded = guard->in_front_of(handler());
    auto connection = new_connection();
    const auto response = answer(guarded, request_of("GET"), connection);

    EXPECT_EQ(response.headers().get("Content-Digest"), served_digest);
    EXPECT_TRUE(is_signed_by_node(response));
}

TEST_F(SignatureGuardTest, AnswerToHeadIsSignedWithoutItsBody) {
    const auto guard = new_guard();
    const auto guarded = guard->in_front_of(handler());
    auto connection = new_connection();
    auto response = answer(guarded, request_of("HEAD"), connection);

    EXPECT_EQ(response.status_code(), 200);
    // The body stays, for the server to give its length, and is not sent.
    EXPECT_EQ(response.body(), served);
    EXPECT_FALSE(response.headers().has("Content-Digest"));
    response.set_body("");
    EXPECT_TRUE(is_signed_by_node(response));
}

TEST_F(SignatureGuardTest, VerifiedRequestIsServedAsFromItsSigner) {
    const auto guard = new_guard();
    const auto guarded = guard->in_front_of(handler());
    const auto peer = new_peer("peer");
    auto connection = new_connection();

    obtain_key(peer);

    const auto put =
        answer(guarded, signed_by(peer, request_of("PUT", hello_path, "hello")), connection);
    const auto post =
        answer(guarded, signed_by(peer, request_of("POST", "/data/nodes", "{}")), connection);

    EXPECT_EQ(put.status_code(), 200);
    EXPECT_EQ(post.status_code(), 200);
    EXPECT_EQ(node_of(connection), peer.id().to_string());
    EXPECT_FALSE(connection.is_closing());
    EXPECT_TRUE(is_signed_by_node(put));
    EXPECT_EQ(served_count(), 2U);
}

TEST_F(SignatureGuardTest, SignerWithoutKeyIsTrustedForAFewRequests) {
    const auto guard = new_guard();
    const auto guarded = guard->in_front_of(handler());
    const auto peer = new_peer("peer");
    auto connection = new_connection();
    const auto served_statuses = statuses(guarded, {&peer, &peer, &peer}, connection);

    EXPECT_EQ(served_statuses, (Statuses{200, 200, 200}));
    EXPECT_FALSE(connection.node().has_value());
    EXPECT_FALSE(connection.is_closing());

    const auto refused = answer(guarded, signed_by(peer, request_of("GET")), connection);

    EXPECT_EQ(refused.status_code(), 401);
    EXPECT_EQ(problem_type(refused), invalid_signature);
    EXPECT_TRUE(connection.is_closing());
    EXPECT_TRUE(is_signed_by_node(refused));
    EXPECT_EQ(served_count(), 3U);
}

TEST_F(SignatureGuardTest, SignerWithoutKeyIsCountedAcrossConnections) {
    const auto guard = new_guard(SignatureGuard::min_provisional_trust_attempts);
    const auto guarded = guard->in_front_of(handler());
    const auto peer = new_peer("peer");
    auto first = new_connection();
    auto second = new_connection();

    EXPECT_EQ(statuses(guarded, {&peer, &peer}, first), (Statuses{200, 200}));
    EXPECT_EQ(statuses(guarded, {&peer}, second), (Statuses{401}));
    EXPECT_FALSE(first.is_closing());
    EXPECT_TRUE(second.is_closing());
}

TEST_F(SignatureGuardTest, EachSignerWithoutKeyIsCounted) {
    const auto guard = new_guard(SignatureGuard::min_provisional_trust_attempts);
    const auto guarded = guard->in_front_of(handler());
    const auto first = new_peer("first");
    const auto second = new_peer("second");
    auto connection = new_connection();

    EXPECT_EQ(statuses(guarded, {&first, &second, &first, &second}, connection),
              (Statuses{200, 200, 200, 200}));
    EXPECT_FALSE(connection.is_closing());
}

TEST_F(SignatureGuardTest, SignerIsServedOnceItsKeyIsHeld) {
    const auto guard = new_guard();
    const auto guarded = guard->in_front_of(handler());
    const auto peer = new_peer("peer");
    auto connection = new_connection();

    EXPECT_EQ(statuses(guarded, {&peer, &peer, &peer}, connection), (Statuses{200, 200, 200}));
    obtain_key(peer);
    EXPECT_EQ(statuses(guarded, {&peer, &peer, &peer}, connection), (Statuses{200, 200, 200}));
    EXPECT_EQ(node_of(connection), peer.id().to_string());
    EXPECT_FALSE(connection.is_closing());
}

TEST_F(SignatureGuardTest, SignerSeenLeastRecentlyIsForgotten) {
    const auto guard = new_guard(SignatureGuard::min_provisional_trust_attempts, 1);
    const auto guarded = guard->in_front_of(handler());
    const auto first = new_peer("first");
    const auto second = new_peer("second");
    auto connection = new_connection();

    // Counting second forgets first, whose count then starts again.
    EXPECT_EQ(statuses(guarded, {&first, &first, &second, &first, &first, &first}, connection),
              (Statuses{200, 200, 200, 200, 200, 401}));
    EXPECT_TRUE(connection.is_closing());
}

TEST_F(SignatureGuardTest, SignatureThatFailsClosesTheConnection) {
    const auto guard = new_guard();
    const auto guarded = guard->in_front_of(handler());
    const auto peer = new_peer("peer");
    auto connection = new_connection();
    auto request = signed_by(peer, request_of("GET"));

    obtain_key(peer);
    request.set_uri("/data/seek");

    const auto response = answer(guarded, request, connection);

    EXPECT_EQ(response.status_code(), 401);
    EXPECT_EQ(problem_type(response), invalid_signature);
    EXPECT_TRUE(connection.is_closing());
    EXPECT_FALSE(connection.node().has_value());
    EXPECT_TRUE(is_signed_by_node(response));
    EXPECT_EQ(served_count(), 0U);
}

TEST_F(SignatureGuardTest, SignatureThatFailsWithoutKeyClosesTheConnection) {
    const auto guard = new_guard();
    const auto guarded = guard->in_front_of(handler());
    const auto peer = new_peer("peer");
    auto connection = new_connection();
    auto request = signed_by(peer, request_of("PUT", hello_path, "hello"));

    request.set_body("jello");

    const auto response = answer(guarded, request, connection);

    EXPECT_EQ(response.status_code(), 401);
    EXPECT_EQ(problem_type(response), invalid_signature);
    EXPECT_TRUE(connection.is_closing());
    EXPECT_EQ(served_count(), 0U);
}

TEST_F(SignatureGuardTest, HalfASignatureClosesTheConnection) {
    const auto guard = new_guard();
    const auto guarded = guard->in_front_of(handler());
    const auto peer = new_peer("peer");
    auto connection = new_connection();
    auto request = signed_by(peer, request_of("GET"));

    obtain_key(peer);
    request.headers().remove("Signature-Input");

    const auto response = answer(guarded, request, connection);

    EXPECT_EQ(response.status_code(), 401);
    EXPECT_EQ(problem_type(response), invalid_signature);
    EXPECT_TRUE(connection.is_closing());
    EXPECT_EQ(served_count(), 0U);
}

TEST_F(SignatureGuardTest, WhatTheHandlerThrowsIsThrownOn) {
    const auto guard = new_guard();
    const auto guarded = guard->in_front_of(
        [](const Request& /*request*/, Response& /*response*/, ConnectionInfo& /*connection*/) {
        throw std::runtime_error("The store failed");
    });
    auto connection = new_connection();

    EXPECT_THROW((void)answer(guarded, request_of("GET"), connection), std::runtime_error);
}

TEST_F(SignatureGuardTest, RefusesWhatItCannotWorkWith) {
    EXPECT_THROW((void)new_guard(SignatureGuard::min_provisional_trust_attempts - 1),
                 std::invalid_argument);
    EXPECT_THROW((void)new_guard(SignatureGuard::default_provisional_trust_attempts, 0),
                 std::invalid_argument);
    EXPECT_NE(new_guard(SignatureGuard::min_provisional_trust_attempts, 1), nullptr);
}
