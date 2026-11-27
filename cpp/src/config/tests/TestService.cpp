#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <cstddef>
#include <filesystem>
#include <iterator>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

#include "config/NotOffered.hpp"
#include "config/Service.hpp"
#include "http/BasicCredentials.hpp"
#include "identity/NodeIdentity.hpp"
#include "io/FileReader.hpp"
#include "logger/ILogOutput.hpp"
#include "logger/Logger.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "storage/tests/Fixtures.hpp"
#include "types/Json.hpp"

using config::NotOffered;
using config::Service;
using http::BasicCredentials;
using identity::NodeIdentity;
using io::FileReader;
using logger::ILogOutput;
using logger::Logger;
using net::Address;
using net::ISocket;
using std::optional;
using std::string;
using std::string_view;
using std::vector;
using types::Json;

namespace {

using Port = Address::Port;

// How long to wait, at most, for the service to listen, and how long between
// tries. The service binds when it is made, but listens only once its thread
// has started.
constexpr auto wait_timeout = std::chrono::seconds(30);
constexpr auto retry_interval = std::chrono::milliseconds(5);

// How many bytes to read from a connection at a time.
constexpr std::size_t read_size = 4096;

// A main port for the tests that name /config's port, so that it is not used.
constexpr Port main_port = 8080;

// Where the main port listens: every interface.
constexpr string_view main_host = "::";

// The endpoint the node names itself at when it does not know the address it
// is reached at (HTTP API §10.1).
constexpr string_view own_endpoint = "http://localhost:8080";

// The highest port there is.
constexpr Port highest_port = 65'535;

// Has the system pick /config's port.
constexpr optional<Port> any_port = 0;

constexpr string_view admin = "admin:secret";

// What has a browser ask for a username and password (RFC 7617 §2, §2.1).
constexpr string_view challenge = R"(Basic realm="Libranet /config", charset="UTF-8")";

// Where the main port listens, on port.
Address main_at(Port port) {
    return {main_host, port};
}

// Those the node list names the node at.
std::vector<string> own_endpoints() {
    return {string(own_endpoint)};
}

// The hosts /config is served as by default, as network.config_hosts names
// them.
vector<string> default_hosts() {
    return {"localhost", "127.0.0.1", "::1"};
}

// The headers of a request from a page of /config, each ending in CRLF. The
// port Host names is not compared with the one the service listens on.
constexpr string_view from_config_page =
    "Host: 127.0.0.1:8180\r\nReferer: http://127.0.0.1:8180/config/\r\n";

// The reference node's Content-Security-Policy (HTTP API §2.3.3).
constexpr string_view policy =
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'";

// The Authorization header giving user_pass, ending in CRLF.
string authorization(string_view user_pass) {
    return string(BasicCredentials::field_name) + ": "
           + BasicCredentials(user_pass).to_string() + "\r\n";
}

// A request for target, by method, with no body, carrying headers, each
// ending in CRLF, on a connection the service closes once it answers.
string request_with(string_view method, string_view target, string_view headers) {
    return string(method) + " " + string(target) + " HTTP/1.1\r\n" + string(headers)
           + "Connection: close\r\nContent-Length: 0\r\n\r\n";
}

// A request for target, by method, from a page of /config, giving the
// credential user_pass.
string request_of(string_view method, string_view target, string_view user_pass = admin) {
    return request_with(method, target, string(from_config_page) + authorization(user_pass));
}

string get(string_view target) {
    return request_of("GET", target);
}

// Whether answer carries the header name, with value.
bool has_header(const string& answer, string_view name, string_view value) {
    return string::npos != answer.find("\r\n" + string(name) + ": " + string(value) + "\r\n");
}

// A connection to address, once the service there is listening.
ISocket::Ptr connect_when_listening(const Address& address) {
    const auto deadline = std::chrono::steady_clock::now() + wait_timeout;

    while (true) {
        try {
            return ISocket::connect(address);
        } catch (const std::system_error&) {
            if (std::chrono::steady_clock::now() >= deadline) {
                throw;
            }
        }

        std::this_thread::sleep_for(retry_interval);
    }
}

// What the service at address answers to request, sent on a connection of
// its own, until it closes the connection.
string answer_to(const Address& address, string_view request) {
    const auto connection = connect_when_listening(address);
    string answer;
    std::vector<std::byte> buffer(read_size);

    connection->send_all(request);

    while (connection->receive(buffer)) {
        std::transform(buffer.begin(), buffer.end(), std::back_inserter(answer),
                       [](std::byte byte) {
            return static_cast<char>(std::to_integer<unsigned char>(byte));
        });
        buffer.resize(read_size);
    }

    return answer;
}

// The body of answer, after its head.
string body_of(const string& answer) {
    const auto end_of_head = answer.find("\r\n\r\n");

    return string::npos == end_of_head ? "" : answer.substr(end_of_head + 4);
}

// Whether answer's status line is HTTP/1.1 and status.
bool has_status(const string& answer, string_view status) {
    return 0 == answer.rfind("HTTP/1.1 " + string(status) + "\r\n", 0);
}

// Whether answer is 404, about:blank, with detail.
bool is_not_found_saying(const string& answer, string_view detail) {
    if (!has_status(answer, "404 Not Found")
        || !has_header(answer, "Content-Type", "application/problem+json")) {
        return false;
    }

    const auto problem = Json::parse(body_of(answer));

    return problem.at("type").string() == "about:blank" && problem.at("detail").string() == detail;
}

// A socket listening at the loopback address, on a port the system picks.
ISocket::Ptr listening() {
    auto socket = ISocket::bind({Service::listen_host, 0});

    socket->listen(1);
    return socket;
}

// A socket listening as listening()'s does, on a port with another
// Service::port_step above it.
ISocket::Ptr listening_below_another() {
    auto socket = listening();

    while (socket->local_address().port() > highest_port - Service::port_step) {
        socket = listening();
    }

    return socket;
}

// A port at the loopback address that nothing listens on now.
Port free_port() {
    return listening()->local_address().port();
}

// The port port_step below port.
Port below(Port port) {
    return static_cast<Port>(port - Service::port_step);
}

// The lines the log writes, from any thread.
struct KeptLines {
    std::mutex lock;
    std::vector<string> lines;
};

// Keeps each line it is given, behind a lock, as the service logs from a
// thread of its own.
class LockedOutput : public ILogOutput {
public:
    explicit LockedOutput(std::shared_ptr<KeptLines> kept)
        : m_kept(std::move(kept)) {}

    void log(string_view line) override {
        const std::scoped_lock guard(m_kept->lock);

        m_kept->lines.emplace_back(line);
    }

private:
    std::shared_ptr<KeptLines> m_kept;
};

// Each test has a data directory, holding the node's key, whose public key
// is in a store, and a service with a port the system picked. It keeps what
// is logged.
class ConfigServiceTest : public testing::Test {
protected:
    void SetUp() override {
        Logger::remove_all_outputs();
        Logger::add_output(std::make_unique<LockedOutput>(m_kept));
        Logger::reset_levels(Logger::Level::Info);
        m_service = std::make_unique<Service>(m_identity, data_dir(), main_at(main_port),
                                              own_endpoints(), any_port, default_hosts());
    }

    void TearDown() override {
        m_service.reset();
        Logger::remove_all_outputs();
    }

    [[nodiscard]] const Address& address() const noexcept {
        return m_service->local_address();
    }

    [[nodiscard]] const std::filesystem::path& data_dir() const noexcept {
        return m_directory.path();
    }

    [[nodiscard]] const NodeIdentity& identity() const noexcept {
        return m_identity;
    }

    [[nodiscard]] bool captured() const {
        return std::filesystem::exists(data_dir() / "config-credential");
    }

    // Whether the service signed answer as the node.
    [[nodiscard]] bool is_signed_by_node(const string& answer) const {
        return string::npos != answer.find(";keyid=\"" + m_identity.id().to_string() + "\"");
    }

    // Whether a line the log wrote holds text.
    [[nodiscard]] bool logged(string_view text) const {
        const std::scoped_lock guard(m_kept->lock);

        return std::any_of(m_kept->lines.begin(), m_kept->lines.end(),
                           [text](const string& line) {
            return string::npos != line.find(text);
        });
    }

    // Each line the log wrote, one after another.
    [[nodiscard]] string log_text() const {
        const std::scoped_lock guard(m_kept->lock);
        string text;

        for (const auto& line : m_kept->lines) {
            text += line + "\n";
        }

        return text;
    }

    void stop() noexcept {
        m_service.reset();
    }

private:
    fixtures::ScratchDirectory m_directory;
    std::shared_ptr<fixtures::MemorySource> m_store = std::make_shared<fixtures::MemorySource>();
    NodeIdentity m_identity{m_directory.path() / "node-key.pem", *m_store};
    std::shared_ptr<KeptLines> m_kept = std::make_shared<KeptLines>();
    std::unique_ptr<Service> m_service;
};

} // namespace

TEST_F(ConfigServiceTest, ListensAtTheLoopbackAddressAlone) {
    EXPECT_EQ(address().host(), "127.0.0.1");
    EXPECT_NE(address().port(), 0);
}

TEST_F(ConfigServiceTest, LogsWhereConfigIs) {
    const auto where = "/config is at http://127.0.0.1:" + std::to_string(address().port())
                       + "/config/";

    EXPECT_TRUE(logged(where)) << log_text();
}

TEST_F(ConfigServiceTest, ListensOnThePortNamed) {
    const auto port = free_port();
    const Service named(identity(), data_dir(), main_at(main_port), own_endpoints(), port,
                        default_hosts());

    EXPECT_EQ(named.local_address().port(), port);
}

// With no port named, it takes the main port's plus 100 when nothing has
// it.
TEST_F(ConfigServiceTest, TakesTheFirstPortAboveTheMainPortWhenNoneIsNamed) {
    const auto port = free_port();
    const Service above(identity(), data_dir(), main_at(below(port)), own_endpoints(),
                        std::nullopt, default_hosts());

    EXPECT_EQ(above.local_address().port(), port);
}

// The port 100 above the main port is taken, so it takes one of 200 above,
// 300 above, and so on.
TEST_F(ConfigServiceTest, PassesOverAPortTaken) {
    const auto taken = listening_below_another();
    const auto taken_port = taken->local_address().port();
    const auto main = below(taken_port);
    const Service above(identity(), data_dir(), main_at(main), own_endpoints(), std::nullopt,
                        default_hosts());
    const auto port = above.local_address().port();

    EXPECT_GT(port, taken_port);
    EXPECT_EQ((port - main) % Service::port_step, 0);
    EXPECT_TRUE(logged("Cannot listen for /config on port " + std::to_string(taken_port)))
        << log_text();
    EXPECT_TRUE(logged("/config is at http://127.0.0.1:" + std::to_string(port) + "/config/"))
        << log_text();
}

// A port named is that port or none.
TEST_F(ConfigServiceTest, RefusesAPortNamedThatIsTaken) {
    const auto taken = listening();

    EXPECT_THROW(const Service refused(identity(), data_dir(), main_at(main_port),
                                       own_endpoints(), taken->local_address().port(),
                                       default_hosts()),
                 std::system_error);
}

TEST_F(ConfigServiceTest, RefusesAMainPortLeavingNoPortAboveIt) {
    constexpr Port above_the_highest_main = highest_port - Service::port_step + 1;

    EXPECT_THROW(const Service refused(identity(), data_dir(), main_at(above_the_highest_main),
                                       own_endpoints(), std::nullopt, default_hosts()),
                 std::invalid_argument);
}

TEST_F(ConfigServiceTest, RefusesANodeNamedAtNoEndpoint) {
    EXPECT_THROW(const Service refused(identity(), data_dir(), main_at(main_port), {}, any_port,
                                       default_hosts()),
                 std::invalid_argument);
}

TEST_F(ConfigServiceTest, ListsItsEndpoints) {
    const auto answer = answer_to(address(), get("/config/api"));

    ASSERT_TRUE(has_status(answer, "200 OK")) << answer;
    EXPECT_TRUE(has_header(answer, "Content-Type", "application/json")) << answer;
    EXPECT_TRUE(is_signed_by_node(answer)) << answer;

    const auto endpoints = Json::parse(body_of(answer)).at("endpoints");

    ASSERT_EQ(endpoints.size(), 2U) << answer;
    EXPECT_EQ(endpoints.at(0).at("method").string(), "GET");
    EXPECT_EQ(endpoints.at(0).at("path").string(), "/config/api");
    EXPECT_FALSE(endpoints.at(0).at("description").string().empty());
    EXPECT_EQ(endpoints.at(1).at("method").string(), "GET");
    EXPECT_EQ(endpoints.at(1).at("path").string(), "/config/api/node");
    EXPECT_FALSE(endpoints.at(1).at("description").string().empty());
}

TEST_F(ConfigServiceTest, ListsItsEndpointsWhateverTheQuery) {
    const auto answer = answer_to(address(), get("/config/api?x=1"));

    EXPECT_TRUE(has_status(answer, "200 OK")) << answer;
}

TEST_F(ConfigServiceTest, RefusesAnyOtherMethodOfItsEndpoints) {
    for (const auto* const method : {"POST", "PUT", "DELETE", "HEAD"}) {
        const auto answer = answer_to(address(), request_of(method, "/config/api"));

        EXPECT_TRUE(has_status(answer, "405 Method Not Allowed")) << method << answer;
        EXPECT_TRUE(has_header(answer, "Allow", "GET")) << method << answer;
        EXPECT_TRUE(is_signed_by_node(answer)) << method << answer;
    }
}

TEST_F(ConfigServiceTest, SaysWhatTheNodeIs) {
    const auto answer = answer_to(address(), get("/config/api/node"));

    ASSERT_TRUE(has_status(answer, "200 OK")) << answer;
    EXPECT_TRUE(has_header(answer, "Content-Type", "application/json")) << answer;
    EXPECT_TRUE(is_signed_by_node(answer)) << answer;

    const auto node = Json::parse(body_of(answer));

    EXPECT_EQ(node.at("node_id").string(), identity().id().to_string());
    EXPECT_EQ(node.at("listen_address").string(), main_host);
    EXPECT_EQ(node.at("listen_port").integer(), main_port);
    EXPECT_EQ(node.at("advertised_endpoint").string(), own_endpoint);
}

TEST_F(ConfigServiceTest, RefusesAnyOtherMethodOfTheNode) {
    const auto answer = answer_to(address(), request_of("POST", "/config/api/node"));

    EXPECT_TRUE(has_status(answer, "405 Method Not Allowed")) << answer;
    EXPECT_TRUE(has_header(answer, "Allow", "GET")) << answer;
    EXPECT_TRUE(is_signed_by_node(answer)) << answer;
}

TEST_F(ConfigServiceTest, ChallengesARequestForTheNodeWithoutTheCredential) {
    const auto answer =
        answer_to(address(), request_with("GET", "/config/api/node", from_config_page));

    EXPECT_TRUE(has_status(answer, "401 Unauthorized")) << answer;
    EXPECT_TRUE(has_header(answer, "WWW-Authenticate", challenge)) << answer;
}

// A path beneath /config that is outside /config/api, as /config/apix is, is
// the page.
TEST_F(ConfigServiceTest, ServesThePage) {
    const auto page = FileReader(LIBRANET_CONFIG_PAGE).read_all();

    for (const auto* const target :
         {"/config", "/config/", "/config/index.html", "/config/x?y=1", "/config/apix"}) {
        const auto answer = answer_to(address(), get(target));

        EXPECT_TRUE(has_status(answer, "200 OK")) << target;
        EXPECT_TRUE(has_header(answer, "Content-Type", "text/html; charset=utf-8")) << target;
        EXPECT_TRUE(is_signed_by_node(answer)) << target;
        EXPECT_EQ(body_of(answer), page) << target;
    }
}

TEST_F(ConfigServiceTest, RefusesAnyOtherMethodOfThePage) {
    for (const auto* const method : {"POST", "PUT", "DELETE", "HEAD"}) {
        const auto answer = answer_to(address(), request_of(method, "/config/"));

        EXPECT_TRUE(has_status(answer, "405 Method Not Allowed")) << method << answer;
        EXPECT_TRUE(has_header(answer, "Allow", "GET")) << method << answer;
        EXPECT_TRUE(is_signed_by_node(answer)) << method << answer;
    }
}

// The other endpoints beneath /config/api come later.
TEST_F(ConfigServiceTest, ServesNothingElseBeneathItsEndpoints) {
    for (const auto* const target : {"/config/api/", "/config/api/node/", "/config/api/nodes"}) {
        const auto answer = answer_to(address(), get(target));

        EXPECT_TRUE(has_status(answer, "404 Not Found")) << target << answer;
        EXPECT_TRUE(is_signed_by_node(answer)) << target << answer;
    }
}

// Each section of the shared page that a later phase brings shows what the
// node answers.
TEST_F(ConfigServiceTest, SaysWhatTheNodeDoesNotOfferYet) {
    struct Asked {
        string_view method;
        string_view target;
        string_view detail;
    };

    for (const auto& asked : {
             Asked{"GET", "/config/api/metadata", "This node does not offer metadata yet."},
             Asked{
                 "DELETE",
                 "/config/api/trust/sha256/abc",
                 "This node does not offer trust lists yet.",
             },
             Asked{
                 "PUT",
                 "/config/api/connecting",
                 "This node does not offer connecting only to the nodes it trusts yet.",
             },
             Asked{"POST", "/config/api/karma/payments", "This node does not offer Karma yet."},
             Asked{
                 "PATCH",
                 "/config/api/applications/notes",
                 "This node does not offer applications yet.",
             },
             Asked{"POST", "/config/api/users", "This node does not offer users yet."},
             Asked{"POST", "/config/api/builds", "This node does not offer builds yet."},
             Asked{"POST", "/config/api/exports", "This node does not offer exports yet."},
             Asked{"POST", "/config/api/backups/job/run", "This node does not offer backups yet."},
             Asked{"GET", "/config/api/restores?x=1", "This node does not offer restores yet."},
         }) {
        const auto answer = answer_to(address(), request_of(asked.method, asked.target));

        EXPECT_TRUE(is_not_found_saying(answer, asked.detail)) << asked.target << answer;
        EXPECT_TRUE(is_signed_by_node(answer)) << asked.target << answer;
    }
}

// Only the paths beneath those the page uses beneath them are what the node
// does not offer yet.
TEST_F(ConfigServiceTest, ServesNothingBeneathAPathNotOfferedThatThePageUsesAlone) {
    for (const auto* const target : {"/config/api/metadata/x", "/config/api/usersx"}) {
        const auto answer = answer_to(address(), get(target));

        EXPECT_TRUE(is_not_found_saying(answer, "No resource exists at this path."))
            << target << answer;
    }
}

TEST_F(ConfigServiceTest, DoesNotListWhatTheNodeDoesNotOfferYet) {
    const auto answer = answer_to(address(), get("/config/api"));
    const auto endpoints = Json::parse(body_of(answer)).at("endpoints");

    ASSERT_TRUE(has_status(answer, "200 OK")) << answer;

    for (const auto& endpoint : endpoints.array()) {
        const auto& path = endpoint.at("path").string();

        for (const auto& feature : NotOffered::features) {
            EXPECT_NE(path, feature.path) << answer;
        }
    }
}

TEST_F(ConfigServiceTest, ServesNothingOutsideConfig) {
    for (const auto* const target : {"/", "/data/nodes", "/data/seek", "/configuration"}) {
        const auto answer = answer_to(address(), get(target));

        EXPECT_TRUE(has_status(answer, "404 Not Found")) << target << answer;
        EXPECT_TRUE(is_signed_by_node(answer)) << target << answer;
    }
}

TEST_F(ConfigServiceTest, CapturesTheFirstCredentialGiven) {
    EXPECT_FALSE(captured());

    const auto answer = answer_to(address(), get("/config/api"));

    EXPECT_TRUE(has_status(answer, "200 OK")) << answer;
    EXPECT_TRUE(captured());
}

// Every path is behind the guard, those outside /config among them, and its
// challenge is signed as every answer is.
TEST_F(ConfigServiceTest, ChallengesARequestWithoutTheCredential) {
    for (const auto* const target :
         {"/config/api", "/config/", "/config/api/backups", "/data/nodes"}) {
        const auto answer = answer_to(address(), request_with("GET", target, from_config_page));

        EXPECT_TRUE(has_status(answer, "401 Unauthorized")) << target << answer;
        EXPECT_TRUE(has_header(answer, "WWW-Authenticate", challenge)) << target << answer;
        EXPECT_TRUE(is_signed_by_node(answer)) << target << answer;
    }

    EXPECT_FALSE(captured());
}

TEST_F(ConfigServiceTest, ChallengesACredentialThatDoesNotMatch) {
    (void)answer_to(address(), get("/config/api"));

    const auto answer = answer_to(address(), request_of("GET", "/config/api", "admin:other"));

    EXPECT_TRUE(has_status(answer, "401 Unauthorized")) << answer;
    EXPECT_TRUE(has_header(answer, "WWW-Authenticate", challenge)) << answer;
}

// With the credential removed, the next one given is taken (HTTP API §2.3.2).
TEST_F(ConfigServiceTest, CapturesANewCredentialOnceTheFileIsRemoved) {
    (void)answer_to(address(), get("/config/api"));
    std::filesystem::remove(data_dir() / "config-credential");

    const auto answer = answer_to(address(), request_of("GET", "/config/api", "admin:other"));

    EXPECT_TRUE(has_status(answer, "200 OK")) << answer;
}

TEST_F(ConfigServiceTest, StopsWithinFiveSecondsWithAClientConnected) {
    constexpr auto most = std::chrono::seconds(5);
    const auto client = connect_when_listening(address());

    client->send_all("GET /config/");

    const auto stopping = std::chrono::steady_clock::now();

    stop();

    EXPECT_LT(std::chrono::steady_clock::now() - stopping, most);
}

// Before the credential is looked at, so that none is captured, and signed
// as every answer is (HTTP API §2.3.3).
TEST_F(ConfigServiceTest, RefusesAnotherSitesPage) {
    const vector<string> refused{
        "Host: attacker.example:8180\r\nReferer: http://attacker.example:8180/config/\r\n",
        string(from_config_page) + "Sec-Fetch-Site: cross-site\r\n",
        string(from_config_page) + "Origin: http://127.0.0.1:8080\r\n",
        "Host: 127.0.0.1:8180\r\n",
        "Host: 127.0.0.1:8180\r\nReferer: http://127.0.0.1:8080/\r\n",
    };

    for (const auto& headers : refused) {
        const auto request = request_with("GET", "/config/api", headers + authorization(admin));
        const auto answer = answer_to(address(), request);

        EXPECT_TRUE(has_status(answer, "403 Forbidden")) << headers << answer;
        EXPECT_TRUE(is_signed_by_node(answer)) << headers << answer;
    }

    EXPECT_FALSE(captured());
}

// The root application's link to /config/ from the main port.
TEST_F(ConfigServiceTest, ServesAPageAPageOfTheSameSiteOpensInAWindow) {
    const string opened = "Host: 127.0.0.1:8180\r\nSec-Fetch-Site: same-site\r\n"
                          "Sec-Fetch-Mode: navigate\r\nSec-Fetch-Dest: document\r\n";
    const auto answer =
        answer_to(address(), request_with("GET", "/config/", opened + authorization(admin)));

    EXPECT_TRUE(has_status(answer, "200 OK")) << answer;
    EXPECT_TRUE(captured());
}

TEST_F(ConfigServiceTest, IsServedAsTheHostsItIsGiven) {
    const Service named(identity(), data_dir(), main_at(main_port), own_endpoints(), any_port,
                        {"node.example"});
    const auto served = answer_to(named.local_address(),
                                  request_with("GET", "/config/api",
                                               "Host: node.example:8180\r\n"
                                               "Referer: http://node.example:8180/config/\r\n"
                                                   + authorization(admin)));
    const auto refused = answer_to(named.local_address(),
                                   request_with("GET", "/config/api",
                                                "Host: other.example:8180\r\n"
                                                "Referer: http://other.example:8180/config/\r\n"
                                                    + authorization(admin)));

    EXPECT_TRUE(has_status(served, "200 OK")) << served;
    EXPECT_TRUE(has_status(refused, "403 Forbidden")) << refused;
}

// HTTP API §2.3.3.
TEST_F(ConfigServiceTest, EveryAnswerCarriesThePolicyAndGrantsNoOtherOrigin) {
    const vector<string> requests{
        get("/config/api"),
        request_of("GET", "/config/api", "admin:other"),
        request_of("POST", "/config/api"),
        get("/data/nodes"),
        request_with("GET", "/config/api", "Host: attacker.example\r\n"),
    };

    for (const auto& request : requests) {
        const auto answer = answer_to(address(), request);

        EXPECT_TRUE(has_header(answer, "Content-Security-Policy", policy)) << request << answer;
        EXPECT_EQ(answer.find("Access-Control-"), string::npos) << request << answer;
    }
}
