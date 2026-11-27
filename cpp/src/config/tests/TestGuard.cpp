#include <gtest/gtest.h>
#include <cstddef>
#include <filesystem>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "config/Credential.hpp"
#include "config/Guard.hpp"
#include "http/BasicCredentials.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "net/Address.hpp"
#include "storage/tests/Fixtures.hpp"
#include "types/Json.hpp"
#include "web/ConnectionInfo.hpp"

using config::Credential;
using config::Guard;
using http::BasicCredentials;
using http::Request;
using http::Response;
using net::Address;
using std::string;
using std::string_view;
using types::Json;
using web::ConnectionInfo;

namespace {

using Status = http::ResponseCode::Status;

constexpr string_view admin = "admin:secret";

// What the handler answers each request it serves with.
constexpr string_view served = "served";

// The problem types the guard answers with (HTTP API §17.2).
constexpr string_view credential_required = "https://libranet.org/problems/credential-required";
constexpr string_view about_blank = "about:blank";

// What has a browser ask for a username and password (RFC 7617 §2, §2.1).
constexpr string_view challenge = R"(Basic realm="Libranet /config", charset="UTF-8")";

// Where a client elsewhere connects from: an address set aside for
// documentation (RFC 5737).
constexpr string_view elsewhere = "192.0.2.1";

// The address of this machine a connection reached: another set aside for
// documentation.
constexpr string_view reached = "198.51.100.1";

// A port the system might pick for a client, and the one /config might take.
constexpr Address::Port client_port = 50'000;
constexpr Address::Port config_port = 8'180;

ConnectionInfo connection_from(string_view source, string_view local = reached) {
    return {Address(source, client_port), Address(local, config_port)};
}

Request request_of(string method, string_view body = "") {
    Request request;

    request.set_method(std::move(method));
    request.set_uri("/config/api");
    request.set_body(string(body));
    return request;
}

// request, with field as its Authorization field's value.
Request giving(string_view field, Request request = request_of("GET")) {
    request.headers().set(string(BasicCredentials::field_name), string(field));
    return request;
}

// The Authorization field's value giving user_pass.
string basic(string_view user_pass) {
    return BasicCredentials(user_pass).to_string();
}

// The type of the problem a response describes.
string problem_type(const Response& response) {
    return Json::parse(response.body()).at("type").string();
}

// Whether response is the 401 asking for the /config credential.
bool is_challenge(const Response& response) {
    return response.code() == Status::Unauthorized && credential_required == problem_type(response)
           && challenge == response.headers().get("WWW-Authenticate", "");
}

// Whether response is the 403 refusing a client elsewhere, without asking
// for the credential.
bool is_refusal(const Response& response) {
    return response.code() == Status::Forbidden && about_blank == problem_type(response)
           && !response.headers().has("WWW-Authenticate");
}

// A handler answering each request with served, counting them in count.
Guard::Handler counting(std::shared_ptr<std::size_t> count) {
    return [count = std::move(count)](const Request&, Response& response, ConnectionInfo&) {
        ++*count;
        response.set_body(string(served));
    };
}

// Each test has a data directory for the credential, and a handler, counting
// the requests it serves, behind a guard that is gone once it is made.
class GuardTest : public testing::Test {
protected:
    // The answer to request, which came on connection.
    [[nodiscard]] Response answer(const Request& request,
                                  ConnectionInfo connection = connection_from("127.0.0.1")) const {
        Response response;

        m_guarded(request, response, connection);
        return response;
    }

    [[nodiscard]] std::size_t served_count() const noexcept {
        return *m_served;
    }

    [[nodiscard]] const Credential& credential() const noexcept {
        return m_credential;
    }

    [[nodiscard]] bool captured() const {
        return std::filesystem::exists(m_credential.file());
    }

private:
    fixtures::ScratchDirectory m_directory;
    Credential m_credential{m_directory.path()};
    std::shared_ptr<std::size_t> m_served = std::make_shared<std::size_t>(0);
    Guard::Handler m_guarded = Guard(m_credential).in_front_of(counting(m_served));
};

} // namespace

TEST_F(GuardTest, CapturesTheFirstCredentialGiven) {
    const auto response = answer(giving(basic(admin)));

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.body(), served);
    EXPECT_EQ(served_count(), 1U);
    EXPECT_TRUE(captured());
    EXPECT_TRUE(credential().authenticate(admin));
}

TEST_F(GuardTest, ServesEachRequestGivingTheCredentialCaptured) {
    ASSERT_EQ(answer(giving(basic(admin))).body(), served);

    EXPECT_EQ(answer(giving(basic(admin))).body(), served);
    EXPECT_EQ(answer(giving(basic(admin), request_of("POST", "{}"))).body(), served);
    EXPECT_EQ(served_count(), 3U);
}

// RFC 9110 §11.1.
TEST_F(GuardTest, ReadsTheSchemeWhateverItsCase) {
    auto field = basic(admin);

    field.replace(0, string_view("Basic").size(), "bAsIc");

    EXPECT_EQ(answer(giving(field)).body(), served);
    EXPECT_TRUE(credential().authenticate(admin));
}

TEST_F(GuardTest, ChallengesARequestGivingNoCredential) {
    const auto response = answer(request_of("GET"));

    EXPECT_EQ(response.status_code(), 401);
    EXPECT_EQ(problem_type(response), credential_required);
    EXPECT_EQ(response.headers().get("WWW-Authenticate", ""), challenge);
    EXPECT_EQ(served_count(), 0U);
    EXPECT_FALSE(captured());
}

TEST_F(GuardTest, ChallengesACredentialThatDoesNotMatch) {
    ASSERT_EQ(answer(giving(basic(admin))).body(), served);

    for (const auto* const other : {"admin:other", "other:secret", "admin:"}) {
        EXPECT_TRUE(is_challenge(answer(giving(basic(other))))) << other;
    }

    EXPECT_EQ(served_count(), 1U);
}

// A field that cannot be read gives no credential, so that it never stands
// for one that was not sent.
TEST_F(GuardTest, AFieldThatCannotBeReadCapturesNothing) {
    // Not base64; base64 of "admin", with no ':'; and admin:secret in another
    // scheme, and in none.
    for (const auto* const field :
         {"Basic !!!", "Basic YWRtaW4=", "Bearer YWRtaW46c2VjcmV0", "YWRtaW46c2VjcmV0", ""}) {
        EXPECT_TRUE(is_challenge(answer(giving(field)))) << field;
    }

    EXPECT_EQ(served_count(), 0U);
    EXPECT_FALSE(captured());
}

// A challenged request never reaches the handler, so nothing reads its body.
TEST_F(GuardTest, AChallengedRequestsBodyIsNotRead) {
    constexpr string_view dial = R"({"address": "192.0.2.9:8080"})";

    ASSERT_EQ(answer(giving(basic(admin))).body(), served);

    const auto response = answer(giving(basic("admin:other"), request_of("POST", dial)));

    EXPECT_TRUE(is_challenge(response));
    EXPECT_EQ(response.body().find("192.0.2.9"), string::npos);
    EXPECT_EQ(served_count(), 1U);
}

// 127.0.0.0/8 and ::1, an IPv4 one in its IPv4-mapped form too (HTTP API
// §2.3).
TEST_F(GuardTest, ServesALoopbackClient) {
    for (const auto* const source : {"127.0.0.1", "127.255.0.9", "::1", "::ffff:127.0.0.1"}) {
        EXPECT_EQ(answer(giving(basic(admin)), connection_from(source)).body(), served) << source;
    }

    EXPECT_EQ(served_count(), 4U);
}

// A client on this machine that connects to one of its network addresses
// comes from that address (HTTP API §2.3).
TEST_F(GuardTest, ServesAClientFromTheAddressItsConnectionReached) {
    constexpr auto mapped = "::ffff:198.51.100.1";
    constexpr auto ipv6 = "2001:db8::5";

    EXPECT_EQ(answer(giving(basic(admin)), connection_from(reached, reached)).body(), served);
    EXPECT_EQ(answer(giving(basic(admin)), connection_from(mapped, reached)).body(), served);
    EXPECT_EQ(answer(giving(basic(admin)), connection_from(reached, mapped)).body(), served);
    EXPECT_EQ(answer(giving(basic(admin)), connection_from(ipv6, ipv6)).body(), served);
    EXPECT_EQ(served_count(), 4U);
}

TEST_F(GuardTest, RefusesAClientElsewhereWhateverItCarries) {
    ASSERT_EQ(answer(giving(basic(admin))).body(), served);

    for (const auto* const source : {elsewhere.data(), "::ffff:192.0.2.1", "2001:db8::5"}) {
        EXPECT_TRUE(is_refusal(answer(giving(basic(admin)), connection_from(source)))) << source;
    }

    EXPECT_EQ(served_count(), 1U);
}

// HTTP API §2.3.
TEST_F(GuardTest, AClientElsewhereCapturesNothing) {
    const auto response = answer(giving(basic(admin)), connection_from(elsewhere));

    EXPECT_TRUE(is_refusal(response));
    EXPECT_FALSE(captured());
}

// Only the addresses of the connection tell where its client is, and a name
// does not say which address it is.
TEST_F(GuardTest, RefusesASourceThatIsNoIpAddress) {
    const auto response = answer(giving(basic(admin)), connection_from("localhost", "localhost"));

    EXPECT_TRUE(is_refusal(response));
    EXPECT_FALSE(captured());
}

TEST_F(GuardTest, ThrowsWhatTheCredentialThrows) {
    constexpr std::size_t far_larger_than_a_hash = 64UL * 1024UL;

    fixtures::write_file(credential().file(), string(far_larger_than_a_hash, '$'));

    EXPECT_THROW((void)answer(giving(basic(admin))), std::runtime_error);
    EXPECT_EQ(served_count(), 0U);
}

TEST_F(GuardTest, ThrowsWhatTheHandlerThrows) {
    const auto guarded =
        Guard(credential()).in_front_of([](const Request&, Response&, ConnectionInfo&) {
        throw std::runtime_error("handler");
    });
    auto connection = connection_from("127.0.0.1");
    Response response;

    EXPECT_THROW(guarded(giving(basic(admin)), response, connection), std::runtime_error);
}
