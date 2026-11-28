#include <gtest/gtest.h>
#include <algorithm>
#include <cstddef>
#include <filesystem>
#include <initializer_list>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "config/Credential.hpp"
#include "config/Guard.hpp"
#include "http/BasicCredentials.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "logger/Logger.hpp"
#include "net/Address.hpp"
#include "storage/tests/Fixtures.hpp"
#include "types/Constants.hpp"
#include "types/Json.hpp"
#include "web/ConnectionInfo.hpp"

using config::Credential;
using config::Guard;
using http::BasicCredentials;
using http::Request;
using http::Response;
using logger::Logger;
using net::Address;
using std::string;
using std::string_view;
using std::vector;
using types::Json;
using types::Kilobyte;
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

// The hosts /config is served as by default, as network.config_hosts names
// them.
vector<string> default_hosts() {
    return {"localhost", "127.0.0.1", "::1"};
}

// Where /config is, as a browser on this machine names it, and a page of it.
constexpr string_view config_host = "127.0.0.1:8180";
constexpr string_view config_page = "http://127.0.0.1:8180/config/";

// A request header, by name and value.
struct Header {
    string_view name;
    string_view value;
};

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

// A request for target, by method, carrying headers, and nothing else.
Request bare(string method, string_view target, const vector<Header>& headers = {}) {
    Request request;

    request.set_method(std::move(method));
    request.set_uri(string(target));

    for (const auto& [name, value] : headers) {
        request.headers().set(string(name), string(value));
    }

    return request;
}

// A request for /config/api, by method, with body, from a page of /config,
// as a browser on this machine sends it.
Request request_of(string method, string_view body = "") {
    auto request =
        bare(std::move(method), "/config/api", {{"Host", config_host}, {"Referer", config_page}});

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

// The detail of the problem a response describes.
string problem_detail(const Response& response) {
    return Json::parse(response.body()).at("detail").string();
}

// Whether response is the 403 refusing a request, without asking for the
// credential.
bool is_refusal(const Response& response) {
    return response.code() == Status::Forbidden && about_blank == problem_type(response)
           && !response.headers().has("WWW-Authenticate");
}

// Whether response is the 403 refusing a request for what its header name
// says, naming it.
bool is_refusal_for(const Response& response, string_view name) {
    return is_refusal(response) && string::npos != problem_detail(response).find(name);
}

// A handler answering each request with served, counting them in count.
Guard::Handler counting(std::shared_ptr<std::size_t> count) {
    return [count = std::move(count)](const Request&, Response& response, ConnectionInfo&) {
        ++*count;
        response.set_body(string(served));
    };
}

// Each test has a data directory for the credential, and a handler, counting
// the requests it serves, behind a guard that is gone once it is made. It
// keeps what is logged.
class GuardTest : public testing::Test {
protected:
    void SetUp() override {
        Logger::remove_all_outputs();
        Logger::add_output(std::make_unique<fixtures::KeepingOutput>(m_lines));
        Logger::reset_levels(Logger::Level::Info);
    }

    void TearDown() override {
        Logger::remove_all_outputs();
    }

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

    // Whether a line the log wrote holds each of texts.
    [[nodiscard]] bool logged(std::initializer_list<string_view> texts) const {
        return std::any_of(m_lines->begin(), m_lines->end(), [texts](const string& line) {
            return std::all_of(texts.begin(), texts.end(), [&line](string_view text) {
                return string::npos != line.find(text);
            });
        });
    }

private:
    fixtures::ScratchDirectory m_directory;
    Credential m_credential{m_directory.path()};
    std::shared_ptr<std::size_t> m_served = std::make_shared<std::size_t>(0);
    Guard::Handler m_guarded = Guard(m_credential, default_hosts()).in_front_of(counting(m_served));
    std::shared_ptr<vector<string>> m_lines = std::make_shared<vector<string>>();
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
    constexpr std::size_t far_larger_than_a_hash = 64 * Kilobyte;

    fixtures::write_file(credential().file(), string(far_larger_than_a_hash, '$'));

    EXPECT_THROW((void)answer(giving(basic(admin))), std::runtime_error);
    EXPECT_EQ(served_count(), 0U);
}

TEST_F(GuardTest, ThrowsWhatTheHandlerThrows) {
    const auto guarded = Guard(credential(), default_hosts())
                             .in_front_of([](const Request&, Response&, ConnectionInfo&) {
        throw std::runtime_error("handler");
    });
    auto connection = connection_from("127.0.0.1");
    Response response;

    EXPECT_THROW(guarded(giving(basic(admin)), response, connection), std::runtime_error);
}

// HTTP API §2.3.3: a site can point a name of its own at this machine.
TEST_F(GuardTest, RefusesAHostItIsNotServedAs) {
    auto request = giving(basic(admin));

    request.headers().set("Host", "attacker.example:8180");

    const auto response = answer(request);

    EXPECT_TRUE(is_refusal_for(response, "Host")) << response.body();
    EXPECT_NE(problem_detail(response).find("attacker.example"), string::npos);
    EXPECT_EQ(served_count(), 0U);
    EXPECT_FALSE(captured());
}

TEST_F(GuardTest, ServesAsEachOfItsHostsAndTheAddressReached) {
    const auto guarded = Guard(credential(), {"node.example"})
                             .in_front_of([](const Request&, Response& response, ConnectionInfo&) {
        response.set_body(string(served));
    });

    for (const auto* const host : {"node.example:8180", "NODE.example", "198.51.100.1:8180"}) {
        auto request = giving(basic(admin), bare("GET", "/config/", {{"Host", host}}));
        auto connection = connection_from("127.0.0.1");
        Response response;

        guarded(request, response, connection);
        EXPECT_EQ(response.body(), served) << host;
    }
}

// Another port on this host, the main port among them, is same-site.
TEST_F(GuardTest, RefusesAPageOfAnotherOrigin) {
    for (const auto* const site : {"cross-site", "same-site", "unknown", ""}) {
        auto request = giving(basic(admin));

        request.headers().set("Sec-Fetch-Site", site);

        EXPECT_TRUE(is_refusal_for(answer(request), "Sec-Fetch-Site")) << site;
    }

    EXPECT_EQ(served_count(), 0U);
    EXPECT_FALSE(captured());
}

TEST_F(GuardTest, ServesItsOwnPagesAndWhatThePersonAsksFor) {
    for (const auto* const site : {"same-origin", "none", " Same-Origin "}) {
        auto request = giving(basic(admin));

        request.headers().set("Sec-Fetch-Site", site);

        EXPECT_EQ(answer(request).body(), served) << site;
    }
}

// Where a browser sends no Sec-Fetch-Site; "null" names no host or port.
TEST_F(GuardTest, RefusesAnOriginOtherThanTheHost) {
    for (const auto* const origin :
         {"http://127.0.0.1:8080", "http://localhost:8180", "http://attacker.example", "null"}) {
        auto request = giving(basic(admin));

        request.headers().set("Origin", origin);

        EXPECT_TRUE(is_refusal_for(answer(request), "Origin")) << origin;
    }

    EXPECT_EQ(served_count(), 0U);
}

TEST_F(GuardTest, ServesAnOriginThatIsTheHost) {
    auto request = giving(basic(admin));

    request.headers().set("Origin", "http://127.0.0.1:8180");

    EXPECT_EQ(answer(request).body(), served);
}

// The root application's link to /config from the main port, on a node
// with no credential yet (HTTP API §2.3.3).
TEST_F(GuardTest, ServesAPageAPageOfTheSameSiteOpensInAWindow) {
    for (const auto* const target : {"/config/", "/config", "/config/x?y=1"}) {
        const auto request = bare("GET", target,
                                  {
                                      {"Host", config_host},
                                      {"Sec-Fetch-Site", "Same-Site"},
                                      {"Sec-Fetch-Mode", "navigate"},
                                      {"Sec-Fetch-Dest", " document "},
                                      {"Origin", "http://127.0.0.1:8080"},
                                  });

        EXPECT_EQ(answer(giving(basic(admin), request)).body(), served) << target;
    }

    EXPECT_TRUE(captured());
}

// Only a GET of a page, outside /config/api, opened in a window, not a frame
// nor by a script's request.
TEST_F(GuardTest, RefusesTheSameSiteAnythingElse) {
    const vector<std::pair<string, vector<Header>>> refused{
        {"GET", {{"Sec-Fetch-Mode", "cors"}, {"Sec-Fetch-Dest", "document"}}},
        {"GET", {{"Sec-Fetch-Mode", "navigate"}, {"Sec-Fetch-Dest", "iframe"}}},
        {"GET", {{"Sec-Fetch-Mode", "navigate"}}},
        {"GET", {{"Sec-Fetch-Dest", "document"}}},
        {"POST", {{"Sec-Fetch-Mode", "navigate"}, {"Sec-Fetch-Dest", "document"}}},
        {"HEAD", {{"Sec-Fetch-Mode", "navigate"}, {"Sec-Fetch-Dest", "document"}}},
    };

    for (const auto& [method, headers] : refused) {
        auto request = giving(basic(admin), bare(method, "/config/", headers));

        request.headers().set("Sec-Fetch-Site", "same-site");

        EXPECT_TRUE(is_refusal_for(answer(request), "Sec-Fetch-Site")) << method;
    }

    for (const auto* const target : {"/config/api", "/config/api/node", "/config/%61pi"}) {
        const auto request = bare("GET", target,
                                  {
                                      {"Host", config_host},
                                      {"Referer", config_page},
                                      {"Sec-Fetch-Site", "same-site"},
                                      {"Sec-Fetch-Mode", "navigate"},
                                      {"Sec-Fetch-Dest", "document"},
                                  });

        EXPECT_TRUE(is_refusal_for(answer(giving(basic(admin), request)), "Sec-Fetch-Site"))
            << target;
    }

    EXPECT_EQ(served_count(), 0U);
    EXPECT_FALSE(captured());
}

// HTTP API §2.5: curl -e names the page a script stands in for.
TEST_F(GuardTest, ServesTheApiToEachPageOfConfig) {
    const vector<std::pair<string_view, string_view>> pages{
        {"127.0.0.1:8180", "http://127.0.0.1:8180/config/"},
        {"127.0.0.1:8180", "http://127.0.0.1:8180/config"},
        {"127.0.0.1:8180", " https://127.0.0.1:8180/config/x/y?z=1#w "},
        {"127.0.0.1:8180", "http://127.0.0.1:8180/config/%61"},
        {"localhost:8180", "HTTP://LocalHost:8180/config/"},
        {"localhost", "http://localhost/config/"},
        {"localhost:80", "http://localhost/config/"},
        {"localhost", "https://localhost:443/config/"},
        {"[::1]:8180", "http://[::1]:8180/config/"},
    };

    for (const auto& [host, page] : pages) {
        const auto request = bare("GET", "/config/api", {{"Host", host}, {"Referer", page}});

        EXPECT_EQ(answer(giving(basic(admin), request)).body(), served) << host << " " << page;
    }
}

TEST_F(GuardTest, RefusesTheApiToWhatIsNoPageOfConfig) {
    const vector<std::pair<string_view, string_view>> pages{
        // No Referer, and none that names a page served over HTTP.
        {config_host, ""},
        {config_host, "/config/"},
        {config_host, "ftp://127.0.0.1:8180/config/"},
        {config_host, "127.0.0.1:8180/config/"},
        {config_host, "http:///config/"},
        // A page at another host or port than Host names.
        {config_host, "http://127.0.0.1:8080/config/"},
        {config_host, "http://localhost:8180/config/"},
        {config_host, "http://127.0.0.1/config/"},
        {"localhost", "https://localhost:8180/config/"},
        {"", "http://127.0.0.1:8180/config/"},
        // A page of the same origin, outside /config, or beneath /config/api.
        {config_host, "http://127.0.0.1:8180/"},
        {config_host, "http://127.0.0.1:8180"},
        {config_host, "http://127.0.0.1:8180/configuration"},
        {config_host, "http://127.0.0.1:8180/config/api"},
        {config_host, "http://127.0.0.1:8180/config/api/node"},
        {config_host, "http://127.0.0.1:8180/config/%61pi"},
        {config_host, "http://127.0.0.1:8180/data/nodes"},
    };

    for (const auto& [host, page] : pages) {
        auto request = bare("GET", "/config/api");

        if (!host.empty()) {
            request.headers().set("Host", string(host));
        }

        if (!page.empty()) {
            request.headers().set("Referer", string(page));
        }

        EXPECT_TRUE(is_refusal_for(answer(giving(basic(admin), request)), "Referer"))
            << host << " " << page;
    }

    EXPECT_EQ(served_count(), 0U);
    EXPECT_FALSE(captured());
}

// However its path is spelled, and whatever the method.
TEST_F(GuardTest, AsksEverythingBeneathTheApiForAPageOfConfig) {
    for (const auto* const target :
         {"/config/api", "/config/api/", "/config/api/node", "/config/api?x=1", "/config/%61pi"}) {
        for (const auto* const method : {"GET", "POST", "HEAD"}) {
            const auto request = bare(method, target, {{"Host", config_host}});

            EXPECT_TRUE(is_refusal_for(answer(giving(basic(admin), request)), "Referer"))
                << method << " " << target;
        }
    }
}

// Pages of /config, and what /config does not serve, are opened from
// anywhere: a link, an address typed, or a bookmark.
TEST_F(GuardTest, AsksNothingOutsideTheApiForAReferer) {
    for (const auto* const target : {"/config/", "/config", "/config/apix", "/data/nodes", "/"}) {
        const auto request = bare("GET", target, {{"Host", config_host}});

        EXPECT_EQ(answer(giving(basic(admin), request)).body(), served) << target;
    }
}

// The path could be that of a page the operator would not have repeated.
TEST_F(GuardTest, ARefusalNamesNoPathTheRefererGives) {
    auto request = giving(basic(admin));

    request.headers().set("Referer", "http://127.0.0.1:8080/secret-path");

    const auto response = answer(request);

    ASSERT_TRUE(is_refusal_for(response, "Referer"));
    EXPECT_NE(problem_detail(response).find("127.0.0.1:8080"), string::npos);
    EXPECT_EQ(problem_detail(response).find("secret-path"), string::npos);
    EXPECT_FALSE(logged({"secret-path"}));
}

// Before the credential is looked at, so that none is captured, and one
// that does not match is not challenged (HTTP API §2.3.3).
TEST_F(GuardTest, RefusesAnotherSitesPageWhateverItGives) {
    ASSERT_EQ(answer(giving(basic(admin))).body(), served);

    for (const auto* const field : {"admin:secret", "admin:other"}) {
        auto request = giving(basic(field));

        request.headers().set("Sec-Fetch-Site", "cross-site");

        EXPECT_TRUE(is_refusal_for(answer(request), "Sec-Fetch-Site")) << field;
    }

    auto request = giving(basic("admin:other"));

    request.headers().remove("Referer");
    EXPECT_TRUE(is_refusal_for(answer(request), "Referer"));
    EXPECT_EQ(served_count(), 1U);
}

// The source address is checked before anything a request carries.
TEST_F(GuardTest, RefusesAClientElsewhereBeforeTheOtherChecks) {
    auto request = giving(basic(admin));

    request.headers().set("Host", "attacker.example");

    const auto response = answer(request, connection_from(elsewhere));

    EXPECT_TRUE(is_refusal(response));
    EXPECT_NE(problem_detail(response).find("clients on this machine"), string::npos);
}

TEST_F(GuardTest, LogsEachRefusalAsAWarningNamingTheHeader) {
    const vector<std::pair<Header, string_view>> refused{
        {{"Host", "attacker.example"}, "Host"},
        {{"Sec-Fetch-Site", "cross-site"}, "Sec-Fetch-Site"},
        {{"Origin", "null"}, "Origin"},
        {{"Referer", "http://127.0.0.1:8180/"}, "Referer"},
    };

    for (const auto& [header, name] : refused) {
        auto request = giving(basic(admin));

        request.headers().set(string(header.name), string(header.value));
        ASSERT_TRUE(is_refusal_for(answer(request), name)) << name;
        EXPECT_TRUE(logged({"[WRN]", "/config/api", string("its ") + string(name) + " header"}))
            << name;
    }
}

// HTTP API §2.3.3.
TEST_F(GuardTest, EveryAnswerCarriesThePolicy) {
    auto refused = giving(basic(admin));

    refused.headers().set("Sec-Fetch-Site", "cross-site");

    for (const auto& response :
         {
             answer(giving(basic(admin))),
             answer(giving(basic("admin:other"))),
             answer(refused),
             answer(giving(basic(admin)), connection_from(elsewhere)),
         }) {
        EXPECT_EQ(response.headers().get("Content-Security-Policy", ""), Guard::policy)
            << response.status_code();
    }
}

// Whatever the handler says, and whatever case it says it in.
TEST_F(GuardTest, NoAnswerGrantsACrossOriginRequest) {
    const vector<string> grants{
        "Access-Control-Allow-Origin",
        "access-control-allow-credentials",
        "Access-Control-Allow-Methods",
        "Access-Control-Allow-Headers",
        "Access-Control-Expose-Headers",
        "Access-Control-Max-Age",
        "Access-Control-Allow-Private-Network",
    };
    const auto guarded = Guard(credential(), default_hosts())
                             .in_front_of([&grants](const Request&, Response& response,
                                                    ConnectionInfo&) {
        for (const auto& grant : grants) {
            response.headers().set(grant, "*");
        }

        response.headers().set("Content-Security-Policy", "frame-ancestors *");
        response.set_body(string(served));
    });
    auto connection = connection_from("127.0.0.1");
    Response response;

    guarded(giving(basic(admin)), response, connection);

    ASSERT_EQ(response.body(), served);

    for (const auto& grant : grants) {
        EXPECT_FALSE(response.headers().has(grant)) << grant;
    }

    EXPECT_EQ(response.headers().get("Content-Security-Policy", ""), Guard::policy);
}

TEST_F(GuardTest, TurnsDownAHostHoldingANul) {
    EXPECT_THROW(Guard(credential(), {string("local\0host", 10)}), std::invalid_argument);
}
