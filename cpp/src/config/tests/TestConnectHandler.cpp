#include <gtest/gtest.h>
#include <cstddef>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "config/ConnectHandler.hpp"
#include "config/DialOutcomes.hpp"
#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "msg/AsyncChannel.hpp"
#include "msg/Channel.hpp"
#include "msg/IMessage.hpp"
#include "msg/Message.hpp"
#include "net/Address.hpp"
#include "peer/Connections.hpp"
#include "peer/PeerConnection.hpp"
#include "storage/Identifier.hpp"
#include "types/Json.hpp"
#include "web/ConnectionInfo.hpp"

using config::ConnectHandler;
using config::DialOutcomes;
using http::Problem;
using http::Request;
using http::Response;
using msg::AsyncChannel;
using msg::Channel;
using msg::IMessage;
using msg::value_of;
using net::Address;
using peer::PeerConnection;
using std::mutex;
using std::optional;
using std::scoped_lock;
using std::string;
using std::string_view;
using std::vector;
using storage::Identifier;
using types::Json;
using web::ConnectionInfo;
using namespace std::chrono_literals;
using AddressDialed = DialOutcomes::AddressDialed;
using DialRequested = peer::Connections::DialRequested;
using Outcome = AddressDialed::Outcome;

// The tests take DialRequested, which the handler posts from another file,
// so nothing here would make its message's virtual to_string(), which
// value_of<>() casts to.
template class msg::Message<DialRequested>;

namespace {

// An address as it is dialed, and so as a request is answered with it.
constexpr string_view endpoint = "http://127.0.0.1:8080";

constexpr string_view json = "application/json";

// Timeouts a dial is over well within here: a request waits 1 + 2 * 2 + 5
// ms.
PeerConnection::Limits brief() {
    return {1ms, 2ms, {}};
}

constexpr DialOutcomes::Milliseconds brief_report = 5ms;

// The Python node's, as peer::Connections is given them by default.
PeerConnection::Limits python_limits() {
    return {};
}

// A node reached, as a dial's outcome names it.
Identifier node_reached() {
    return Identifier::of({std::byte{'n'}, std::byte{'o'}, std::byte{'d'}, std::byte{'e'}});
}

// A request for target, by method, with body, of content_type, unless none
// is named.
Request request_of(string_view method, string_view target, optional<string_view> content_type,
                   string body) {
    Request request;

    request.set_method(string(method));
    request.set_uri(string(target));
    request.set_version("HTTP/1.1");

    if (content_type) {
        request.headers().set("Content-Type", string(*content_type));
    }

    request.set_body(std::move(body));
    return request;
}

// A POST of body, of content_type, to the handler's path.
Request post(optional<string_view> content_type, string body) {
    return request_of("POST", ConnectHandler::path, content_type, std::move(body));
}

// A POST asking for address to be dialed.
Request post_address(string_view address) {
    return post(json, R"({"address": ")" + string(address) + R"("})");
}

// What a handler waiting in outcomes answers request with, from a client on
// this machine.
Response answer_to(DialOutcomes& outcomes, const Request& request) {
    constexpr Address::Port client_port = 50'000;
    constexpr Address::Port config_port = 8180;
    const ConnectionInfo connection(Address("127.0.0.1", client_port),
                                    Address("127.0.0.1", config_port));
    const ConnectHandler handler(outcomes);
    Response response;

    handler(request, response, connection);
    return response;
}

// The problem detail response carries, once it is known to be one.
Json problem_of(const Response& response) {
    EXPECT_EQ(response.headers().get("Content-Type", ""), Problem::content_type);
    return Json::parse(response.body());
}

// What response says, as a problem detail.
string detail_of(const Response& response) {
    return problem_of(response).at("detail").string();
}

// Answers each dial asked for as peer::Connections does once the dial is
// over, with an outcome, and keeps each endpoint asked for.
class Dialer {
public:
    explicit Dialer(Outcome outcome, optional<Identifier> node_id = std::nullopt,
                    string detail = "")
        : m_outcome(outcome)
        , m_node_id(std::move(node_id))
        , m_detail(std::move(detail))
        , m_channel(DialRequested::name,
                    [this](const IMessage::Ptr& message) { _answer(message); }) {
    }

    // Each endpoint asked for, in the order asked.
    [[nodiscard]] vector<string> asked() const {
        const scoped_lock lock(m_mutex);

        return m_asked;
    }

    ~Dialer() = default;

    Dialer(const Dialer&) = delete;
    Dialer& operator=(const Dialer&) = delete;
    Dialer(Dialer&&) = delete;
    Dialer& operator=(Dialer&&) = delete;

private:
    Outcome m_outcome;
    optional<Identifier> m_node_id;
    string m_detail;
    mutable mutex m_mutex;
    vector<string> m_asked; // Guarded by m_mutex
    // Made last, and so stopped first.
    AsyncChannel m_channel;

    void _answer(const IMessage::Ptr& message) {
        const auto* const request = value_of<DialRequested>(message);

        if (nullptr == request) {
            return;
        }

        {
            const scoped_lock lock(m_mutex);

            m_asked.push_back(request->endpoint);
        }

        Channel::post(AddressDialed::name,
                      AddressDialed{request->dial_id, m_outcome, m_node_id, m_detail});
    }
};

} // namespace

TEST(ConnectHandlerTest, NodeReachedIsNamed) {
    DialOutcomes outcomes(python_limits());
    const Dialer dialer(Outcome::Connected, node_reached());

    const auto response = answer_to(outcomes, post_address(endpoint));

    ASSERT_EQ(response.status_code(), 200) << response.body();
    EXPECT_EQ(response.headers().get("Content-Type", ""), json);

    const auto answer = Json::parse(response.body());

    EXPECT_EQ(answer.at("address").string(), endpoint);
    EXPECT_EQ(answer.at("node_id").string(), node_reached().to_string());
    EXPECT_EQ(answer.at("outcome").string(), "connected");
    EXPECT_EQ(answer.object().size(), 3U);
}

TEST(ConnectHandlerTest, NodeConnectedAlreadyIsNamed) {
    DialOutcomes outcomes(python_limits());
    const Dialer dialer(Outcome::AlreadyConnected, node_reached());

    const auto response = answer_to(outcomes, post_address(endpoint));

    ASSERT_EQ(response.status_code(), 200) << response.body();

    const auto answer = Json::parse(response.body());

    EXPECT_EQ(answer.at("node_id").string(), node_reached().to_string());
    EXPECT_EQ(answer.at("outcome").string(), "already_connected");
}

// So that the node learns it once however it was typed.
TEST(ConnectHandlerTest, AddressIsDialedAndAnsweredAsItIsWritten) {
    DialOutcomes outcomes(python_limits());
    const Dialer dialer(Outcome::Connected, node_reached());

    const auto response = answer_to(outcomes, post_address(" HTTP://Node.Example:8080/config/ "));

    ASSERT_EQ(response.status_code(), 200) << response.body();
    EXPECT_EQ(Json::parse(response.body()).at("address").string(), "http://node.example:8080");
    EXPECT_EQ(dialer.asked(), vector<string>{"http://node.example:8080"});
}

TEST(ConnectHandlerTest, BodyIsJsonWhateverTheParametersOrCaseOfItsType) {
    DialOutcomes outcomes(python_limits());
    const Dialer dialer(Outcome::Connected, node_reached());

    for (const auto* const content_type :
         {"application/json; charset=utf-8", "Application/JSON", " application/json ;x=y"}) {
        const auto response =
            answer_to(outcomes, post(content_type, R"({"address": "http://127.0.0.1:8080"})"));

        EXPECT_EQ(response.status_code(), 200) << content_type << response.body();
    }
}

// A page on another site can send a form's types without asking the node
// first (HTTP API §2.3.3), so none of them is read.
TEST(ConnectHandlerTest, BodyNotSaidToBeJsonIsRefusedUnread) {
    DialOutcomes outcomes(python_limits());
    const Dialer dialer(Outcome::Connected, node_reached());
    const string body = R"({"address": "http://127.0.0.1:8080"})";

    for (const auto* const content_type :
         {
             "text/plain",
             "application/x-www-form-urlencoded",
             "multipart/form-data",
             "application/jsonx",
             "application/problem+json",
             " ",
         }) {
        const auto response = answer_to(outcomes, post(content_type, body));

        EXPECT_EQ(response.status_code(), 415) << content_type << response.body();
        EXPECT_EQ(problem_of(response).at("type").string(), "about:blank") << content_type;
    }

    EXPECT_TRUE(dialer.asked().empty());
}

TEST(ConnectHandlerTest, RefusalOfABodyNotSaidToBeJsonSaysWhy) {
    DialOutcomes outcomes(python_limits());

    EXPECT_EQ(detail_of(answer_to(outcomes, post("text/plain; charset=utf-8", "{}"))),
              "A request body's Content-Type must be application/json, not text/plain; "
              "charset=utf-8");

    for (const auto content_type : {optional<string_view>(), optional<string_view>(" ")}) {
        EXPECT_EQ(detail_of(answer_to(outcomes, post(content_type, "{}"))),
                  "A request body's Content-Type must be application/json, and this request "
                  "names none");
    }
}

TEST(ConnectHandlerTest, BodyNamingNoAddressTheNodeDialsIsAnInvalidRequest) {
    DialOutcomes outcomes(python_limits());
    const Dialer dialer(Outcome::Connected, node_reached());

    for (const auto* const body :
         {R"({"address": "ftp://example"})", R"({"address": 8080})", "{}", "not JSON", ""}) {
        const auto response = answer_to(outcomes, post(json, body));

        EXPECT_EQ(response.status_code(), 400) << body << response.body();
        EXPECT_EQ(problem_of(response).at("type").string(),
                  "https://libranet.org/problems/invalid-request")
            << body;
    }

    EXPECT_TRUE(dialer.asked().empty());
}

TEST(ConnectHandlerTest, InvalidRequestSaysWhy) {
    DialOutcomes outcomes(python_limits());

    EXPECT_EQ(detail_of(answer_to(outcomes, post(json, R"({"address": "ftp://example"})"))),
              "An address must start with http://, as http://192.168.1.10:8080 does: "
              "ftp://example");
}

TEST(ConnectHandlerTest, ThisNodesOwnAddressIsAConflict) {
    DialOutcomes outcomes(python_limits());
    const Dialer dialer(Outcome::ThisNode, node_reached());

    const auto response = answer_to(outcomes, post_address(endpoint));

    EXPECT_EQ(response.status_code(), 409) << response.body();
    EXPECT_EQ(detail_of(response), "http://127.0.0.1:8080 is this node's own address.");
}

TEST(ConnectHandlerTest, NoNodeReachedIsABadGatewaySayingWhy) {
    DialOutcomes outcomes(python_limits());
    const Dialer dialer(Outcome::Unreached, std::nullopt, "connection refused");

    const auto response = answer_to(outcomes, post_address(endpoint));

    EXPECT_EQ(response.status_code(), 502) << response.body();
    EXPECT_EQ(detail_of(response), "No node was reached at http://127.0.0.1:8080: "
                                   "connection refused");
}

TEST(ConnectHandlerTest, NoNodeReachedForNoReasonGivenSaysSo) {
    DialOutcomes outcomes(python_limits());
    const Dialer dialer(Outcome::Unreached);

    const auto response = answer_to(outcomes, post_address(endpoint));

    EXPECT_EQ(response.status_code(), 502) << response.body();
    EXPECT_EQ(detail_of(response), "No node was reached at http://127.0.0.1:8080: "
                                   "no reason was given");
}

TEST(ConnectHandlerTest, NothingComingInTimeIsAGatewayTimeout) {
    DialOutcomes outcomes(brief(), brief_report);

    const auto response = answer_to(outcomes, post_address(endpoint));

    EXPECT_EQ(response.status_code(), 504) << response.body();
    EXPECT_EQ(detail_of(response), "The node did not say in time whether it reached "
                                   "http://127.0.0.1:8080. It may yet.");
}

// As the node stops, so that it need not wait as long as a dial may take.
TEST(ConnectHandlerTest, NoDialIsAskedForOnceStopped) {
    DialOutcomes outcomes(python_limits());
    const Dialer dialer(Outcome::Connected, node_reached());

    outcomes.stop();

    const auto response = answer_to(outcomes, post_address(endpoint));

    EXPECT_EQ(response.status_code(), 504) << response.body();
    EXPECT_TRUE(dialer.asked().empty());
}

TEST(ConnectHandlerTest, NodeReachedThatIsNotNamedIsUnknown) {
    DialOutcomes outcomes(python_limits());
    const Dialer dialer(Outcome::Connected);

    const auto response = answer_to(outcomes, post_address(endpoint));

    EXPECT_EQ(response.status_code(), 500) << response.body();
    EXPECT_EQ(detail_of(response), "What came of dialing http://127.0.0.1:8080 is unknown.");
}

TEST(ConnectHandlerTest, RefusesAnyOtherMethod) {
    DialOutcomes outcomes(python_limits());
    const Dialer dialer(Outcome::Connected, node_reached());

    for (const auto* const method : {"GET", "PUT", "DELETE", "HEAD", "PATCH"}) {
        const auto response = answer_to(
            outcomes, request_of(method, ConnectHandler::path, json, R"({"address": "x"})"));

        EXPECT_EQ(response.status_code(), 405) << method << response.body();
        EXPECT_EQ(response.headers().get("Allow", ""), "POST") << method;
    }

    EXPECT_TRUE(dialer.asked().empty());
}

TEST(ConnectHandlerTest, ServesItsPathWhateverTheQuery) {
    DialOutcomes outcomes(python_limits());
    const Dialer dialer(Outcome::Connected, node_reached());
    const auto request = request_of("POST", "/config/api/connections?x=1", json,
                                    R"({"address": "http://127.0.0.1:8080"})");

    EXPECT_EQ(answer_to(outcomes, request).status_code(), 200);
}

TEST(ConnectHandlerTest, ServesNothingBeneathItsPath) {
    DialOutcomes outcomes(python_limits());
    const Dialer dialer(Outcome::Connected, node_reached());

    for (const auto* const target : {"/config/api/connections/", "/config/api/connections/x"}) {
        const auto response = answer_to(outcomes, request_of("POST", target, json,
                                                             R"({"address": "http://a"})"));

        EXPECT_EQ(response.status_code(), 404) << target << response.body();
    }

    EXPECT_TRUE(dialer.asked().empty());
}
