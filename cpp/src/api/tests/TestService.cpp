#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <iterator>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

#include "api/Service.hpp"
#include "identity/NodeIdentity.hpp"
#include "logger/ILogOutput.hpp"
#include "logger/Logger.hpp"
#include "msg/Channel.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "protocol/NodeList.hpp"
#include "protocol/SeekList.hpp"
#include "stats/Recorder.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"
#include "storage/SearchAnswered.hpp"
#include "storage/tests/Fixtures.hpp"
#include "types/Converter.hpp"

using api::Service;
using identity::NodeIdentity;
using logger::ILogOutput;
using logger::Logger;
using msg::Channel;
using net::Address;
using net::ISocket;
using protocol::NodeList;
using protocol::SeekList;
using storage::HashPrefix;
using storage::Identifier;
using storage::SearchAnswered;
using types::Converter;

namespace {

using NodesUpdated = stats::Recorder::NodesUpdated;
using SeekUpdated = stats::Recorder::SeekUpdated;
using Settings = Service::Settings;

// How long to wait, at most, for the service to listen, or to serve a list
// posted, and how long between tries. The service binds when it is made,
// but listens only once its thread has started, and takes each list on
// another.
constexpr auto wait_timeout = std::chrono::seconds(30);
constexpr auto retry_interval = std::chrono::milliseconds(5);

// How many bytes to read from a connection at a time.
constexpr std::size_t read_size = 4'096;

// A node whose endpoint the node list may name: an address set aside for
// documentation (RFC 5737), and the SHA-256 of nothing as its id.
constexpr std::string_view peer_endpoint = "http://192.0.2.7:8080";
constexpr std::string_view peer_hash =
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

// The loopback address, at a port the system picks.
Address loopback() {
    return {"127.0.0.1", 0};
}

Identifier hello_id() {
    return {"sha256", fixtures::hello_hash};
}

// How many hex digits a SHA-256 hash has.
constexpr std::size_t hash_digits = 64;

// The prefix of hello's hash that the tests search for.
constexpr std::string_view hello_prefix = "2cf24d";

// Content the store does not hold, whose hash begins as hello's does, so
// that a search for hello's prefix finds it once a peer names it.
Identifier near_hello_id() {
    return {"sha256",
            std::string(hello_prefix) + std::string(hash_digits - hello_prefix.size(), 'a')};
}

// Content the store does not hold, far from hello.
Identifier unheld_id() {
    return {"sha256", std::string(hash_digits, 'f')};
}

// A request for target, by method, unsigned, with body, on a connection
// the service closes once it answers.
std::string request_of(std::string_view method, std::string_view target,
                       std::string_view body = "") {
    return std::string(method) + " " + std::string(target)
           + " HTTP/1.1\r\nConnection: close\r\nContent-Length: " + std::to_string(body.size())
           + "\r\n\r\n" + std::string(body);
}

std::string get(std::string_view target) {
    return request_of("GET", target);
}

// A request for target, by method, unsigned and with no body, carrying
// headers, each ending in CRLF, on a connection the service closes once it
// answers.
std::string request_with(std::string_view method, std::string_view target,
                         std::string_view headers) {
    return std::string(method) + " " + std::string(target) + " HTTP/1.1\r\n"
           + std::string(headers) + "Connection: close\r\nContent-Length: 0\r\n\r\n";
}

// The port the tests tell the service /config is served on.
constexpr std::uint16_t config_port = 8'180;

// Whether answer carries the header name, with value.
bool has_header(const std::string& answer, std::string_view name, std::string_view value) {
    return std::string::npos
           != answer.find("\r\n" + std::string(name) + ": " + std::string(value) + "\r\n");
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
std::string answer_to(const Address& address, std::string_view request) {
    const auto connection = connect_when_listening(address);
    std::string answer;
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
std::string body_of(const std::string& answer) {
    const auto end_of_head = answer.find("\r\n\r\n");

    return std::string::npos == end_of_head ? "" : answer.substr(end_of_head + 4);
}

// Whether answer's status line is HTTP/1.1 and status.
bool has_status(const std::string& answer, std::string_view status) {
    return 0 == answer.rfind("HTTP/1.1 " + std::string(status) + "\r\n", 0);
}

// The body the service at address serves at target, once it is expected,
// or the last it served, if it is not by the time the wait ends.
std::string served_eventually(const Address& address, std::string_view target,
                              const std::string& expected) {
    const auto deadline = std::chrono::steady_clock::now() + wait_timeout;
    auto body = body_of(answer_to(address, get(target)));

    while (body != expected && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(retry_interval);
        body = body_of(answer_to(address, get(target)));
    }

    return body;
}

// The lines the log writes, from any thread.
struct KeptLines {
    std::mutex lock;
    std::vector<std::string> lines;
};

// Keeps each line it is given, behind a lock, as the service logs from a
// thread of its own.
class LockedOutput : public ILogOutput {
public:
    explicit LockedOutput(std::shared_ptr<KeptLines> kept)
        : m_kept(std::move(kept)) {}

    void log(std::string_view line) override {
        const std::scoped_lock guard(m_kept->lock);

        m_kept->lines.emplace_back(line);
    }

private:
    std::shared_ptr<KeptLines> m_kept;
};

// Each test has a service, listening on the loopback address, serving a
// store that holds hello, and the node's key, as the node's identity puts
// it there. It keeps what the service logs.
class ServiceTest : public testing::Test {
protected:
    void SetUp() override {
        Logger::remove_all_outputs();
        Logger::add_output(std::make_unique<LockedOutput>(m_kept));
        m_store->put(hello_id(), Converter::bytes_of(fixtures::hello));
        m_service = std::make_unique<Service>(loopback(), m_store, m_identity, Settings{});
    }

    void TearDown() override {
        m_service.reset();
        Logger::remove_all_outputs();
    }

    [[nodiscard]] const Address& address() const noexcept {
        return m_service->local_address();
    }

    [[nodiscard]] const Identifier& own_id() const noexcept {
        return m_identity.id();
    }

    // The node list the service names itself in, until it is given another.
    [[nodiscard]] NodeList own_list() const {
        return {own_id(), {"http://localhost:" + std::to_string(address().port())}};
    }

    // Whether the service signed answer as the node.
    [[nodiscard]] bool is_signed_by_node(const std::string& answer) const {
        return std::string::npos != answer.find(";keyid=\"" + own_id().to_string() + "\"");
    }

    // Whether a line the log wrote holds text.
    [[nodiscard]] bool logged(std::string_view text) const {
        const std::scoped_lock guard(m_kept->lock);

        return std::any_of(m_kept->lines.begin(), m_kept->lines.end(),
                           [text](const std::string& line) {
            return std::string::npos != line.find(text);
        });
    }

    // Each line the log wrote, one after another.
    [[nodiscard]] std::string log_text() const {
        const std::scoped_lock guard(m_kept->lock);
        std::string text;

        for (const auto& line : m_kept->lines) {
            text += line + "\n";
        }

        return text;
    }

    void stop() noexcept {
        m_service.reset();
    }

    [[nodiscard]] Service& service() const noexcept {
        return *m_service;
    }

private:
    fixtures::ScratchDirectory m_directory;
    std::shared_ptr<fixtures::MemorySource> m_store = std::make_shared<fixtures::MemorySource>();
    NodeIdentity m_identity{m_directory.path() / "node-key.pem", *m_store};
    std::shared_ptr<KeptLines> m_kept = std::make_shared<KeptLines>();
    std::unique_ptr<Service> m_service;
};

} // namespace

TEST_F(ServiceTest, ListensWhereItIsTold) {
    EXPECT_EQ(service().local_address().host(), "127.0.0.1");
    EXPECT_NE(service().local_address().port(), 0);
}

TEST_F(ServiceTest, ServesWhatTheStoreHolds) {
    const auto answer = answer_to(address(), get("/data/" + hello_id().to_string()));

    EXPECT_TRUE(has_status(answer, "200 OK")) << answer;
    EXPECT_TRUE(is_signed_by_node(answer)) << answer;
    EXPECT_EQ(body_of(answer), fixtures::hello);
}

// Until it is given a node list, it names the node alone, at localhost, the
// port it listens on (HttpApi §10.1).
TEST_F(ServiceTest, ServesItsOwnEndpointAsItsNodeList) {
    const auto answer = answer_to(address(), get("/data/nodes"));

    EXPECT_TRUE(has_status(answer, "200 OK")) << answer;
    EXPECT_TRUE(is_signed_by_node(answer)) << answer;
    EXPECT_EQ(body_of(answer), "{\"nodes\":{\"http://localhost:"
                                   + std::to_string(address().port()) + "\":\""
                                   + own_id().to_string() + "\"}}");
}

TEST_F(ServiceTest, NamesItsOwnEndpoints) {
    EXPECT_EQ(service().own_endpoints(),
              std::vector<std::string>{"http://localhost:" + std::to_string(address().port())});
}

TEST_F(ServiceTest, ServesEachNodeListPosted) {
    auto nodes = own_list();

    nodes.add(std::string(peer_endpoint), Identifier("sha256", std::string(peer_hash)));
    Channel::post(NodesUpdated::name, NodesUpdated{nodes, NodeList()});

    EXPECT_EQ(served_eventually(address(), "/data/nodes", nodes.to_string()), nodes.to_string());

    Channel::post(NodesUpdated::name, NodesUpdated{own_list(), NodeList()});

    EXPECT_EQ(served_eventually(address(), "/data/nodes", own_list().to_string()),
              own_list().to_string());
}

TEST_F(ServiceTest, ServesAnEmptySeekListUntilOneIsPosted) {
    const auto answer = answer_to(address(), get("/data/seek"));

    EXPECT_TRUE(has_status(answer, "200 OK")) << answer;
    EXPECT_TRUE(is_signed_by_node(answer)) << answer;
    EXPECT_EQ(body_of(answer), R"({"data":[],"search":[]})");
}

TEST_F(ServiceTest, ServesEachSeekListPosted) {
    const std::string hello_sought = R"({"data":["sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161)"
                                     R"(e5c1fa7425e73043362938b9824"],"search":["2cf2"]})";
    const std::string nothing_sought = R"({"data":[],"search":[]})";

    Channel::post(SeekUpdated::name,
                  SeekUpdated{SeekList({hello_id()}, {HashPrefix::parse("2cf2")})});

    EXPECT_EQ(served_eventually(address(), "/data/seek", hello_sought), hello_sought);

    Channel::post(SeekUpdated::name, SeekUpdated{SeekList()});

    EXPECT_EQ(served_eventually(address(), "/data/seek", nothing_sought), nothing_sought);
}

// A search is answered from what the store holds (HttpApi §6.1).
TEST_F(ServiceTest, AnswersSearchesFromTheStore) {
    const auto answer = answer_to(address(), get("/data/search/2cf24d"));

    EXPECT_TRUE(has_status(answer, "200 OK")) << answer;
    EXPECT_TRUE(is_signed_by_node(answer)) << answer;
    EXPECT_EQ(body_of(answer), R"({"results":[")" + hello_id().to_string() + R"("]})");
}

// What a peer names for a search, as it answers one or names what it holds
// that the seek list's search matches, is listed by a later search, best
// first (HttpApi §6, §10.7.2).
TEST_F(ServiceTest, AddsWhatPeersNameToItsSearches) {
    const auto expected = R"({"results":[")" + near_hello_id().to_string() + R"(",")"
                          + hello_id().to_string() + R"("]})";

    // Asked first, so that the answer kept is of what the store holds.
    EXPECT_EQ(body_of(answer_to(address(), get("/data/search/2cf24d"))),
              R"({"results":[")" + hello_id().to_string() + R"("]})");

    Channel::post(SearchAnswered::name,
                  SearchAnswered{
                      HashPrefix::parse("2cf24d"),
                      Identifier("sha256", std::string(peer_hash)),
                      {near_hello_id()},
                  });

    EXPECT_EQ(served_eventually(address(), "/data/search/2cf24d", expected), expected);
}

// A client asking for content the node will look for is told to retry after
// network.retry_after_seconds (HttpApi §5.2).
TEST_F(ServiceTest, TellsClientsToRetryAfterItsSetting) {
    const fixtures::ScratchDirectory directory;
    const auto store = std::make_shared<fixtures::MemorySource>();
    const NodeIdentity identity(directory.path() / "node-key.pem", *store);
    constexpr auto retry_after = std::chrono::seconds(7);
    Settings settings;

    settings.retry_after = retry_after;

    const Service looking(loopback(), store, identity, settings);
    const auto answer = answer_to(looking.local_address(),
                                  get("/data/" + unheld_id().to_string()));

    EXPECT_TRUE(has_status(answer, "503 Service Unavailable")) << answer;
    EXPECT_NE(std::string::npos, answer.find("Retry-After: 7\r\n")) << answer;
}

// A body larger than storage.max_object_bytes is refused unread, before it
// reaches a handler (HttpApi §7.1).
TEST_F(ServiceTest, RefusesBodiesLargerThanItsSetting) {
    const fixtures::ScratchDirectory directory;
    const auto store = std::make_shared<fixtures::MemorySource>();
    const NodeIdentity identity(directory.path() / "node-key.pem", *store);
    const auto hello_put = request_of("PUT", "/data/" + hello_id().to_string(), fixtures::hello);
    Settings settings;

    settings.object_bytes = std::string_view(fixtures::hello).size() - 1;

    const Service cramped(loopback(), store, identity, settings);
    const auto refused = answer_to(cramped.local_address(), hello_put);

    EXPECT_TRUE(has_status(refused, "413 Payload Too Large")) << refused;
    EXPECT_TRUE(has_status(answer_to(address(), hello_put), "401 Unauthorized"));
}

// Content whose search is held off is not looked for (HttpApi §5.3).
TEST_F(ServiceTest, AnswersNotFoundWhileTheSearchIsHeldOff) {
    const fixtures::ScratchDirectory directory;
    const auto store = std::make_shared<fixtures::MemorySource>();
    const NodeIdentity identity(directory.path() / "node-key.pem", *store);
    Settings settings;

    settings.is_held_off = [](const Identifier& identifier) {
        return identifier.to_string() == unheld_id().to_string();
    };

    const Service held_off(loopback(), store, identity, settings);
    const auto answer = answer_to(held_off.local_address(),
                                  get("/data/" + unheld_id().to_string()));

    EXPECT_TRUE(has_status(answer, "404 Not Found")) << answer;
}

// The guard stands in front of every handler, so an unsigned upload to any
// of them is refused (HandshakeProtocol §2.1).
TEST_F(ServiceTest, RefusesUnsignedUploadsToEachHandler) {
    const std::vector<std::string> uploads = {
        request_of("PUT", "/data/" + hello_id().to_string(), fixtures::hello),
        request_of("POST", "/data/nodes", own_list().to_string()),
        request_of("POST", "/data/seek", R"({"data":[],"search":[]})"),
        request_of("POST", "/data/search/2cf2", R"({"results":[]})"),
    };

    for (const auto& upload : uploads) {
        const auto answer = answer_to(address(), upload);

        EXPECT_TRUE(has_status(answer, "401 Unauthorized")) << upload << answer;
        EXPECT_TRUE(is_signed_by_node(answer)) << upload << answer;
    }
}

// Until it is told where /config is, nothing is served beneath it.
TEST_F(ServiceTest, ServesNothingBeneathConfigUntilToldItsPort) {
    for (const auto* const target : {"/config", "/config/", "/config/api"}) {
        const auto answer = answer_to(address(), get(target));

        EXPECT_TRUE(has_status(answer, "404 Not Found")) << target << answer;
        EXPECT_NE(std::string::npos, body_of(answer).find("No resource exists at this path."))
            << target << answer;
    }
}

// A page of /config, typed or bookmarked on the main port, is sent to
// /config's port, at the host the request named, or at the address /config
// listens at when it names none (HttpApi §2.3).
TEST_F(ServiceTest, SendsAConfigPageToConfigsPortAtTheHostNamed) {
    const std::vector<std::pair<std::string, std::string>> hosts = {
        {"Host: localhost:8080\r\n", "http://localhost:8180"},
        {"Host: 127.0.0.1:8080\r\n", "http://127.0.0.1:8180"},
        {"Host: LocalHost\r\n", "http://localhost:8180"},
        {"Host: [::1]:8080\r\n", "http://[::1]:8180"},
        {"Host: [::1\r\n", "http://127.0.0.1:8180"},
        {"", "http://127.0.0.1:8180"},
    };

    service().set_config_port(config_port);

    for (const auto& [host, origin] : hosts) {
        const auto answer = answer_to(address(), request_with("GET", "/config/", host));

        EXPECT_TRUE(has_status(answer, "302 Found")) << host << answer;
        EXPECT_TRUE(has_header(answer, "Location", origin + "/config/")) << host << answer;
        EXPECT_TRUE(is_signed_by_node(answer)) << host << answer;
        EXPECT_EQ(std::string::npos, answer.find("WWW-Authenticate")) << host << answer;
    }
}

// Each page of /config is sent to the same path on /config's port, its query
// kept.
TEST_F(ServiceTest, SendsEachConfigPageToItsPathOnConfigsPort) {
    const std::vector<std::string> targets = {
        "/config",
        "/config/",
        "/config/index.html",
        "/config/%61pis",
        "/config/?section=node",
    };

    service().set_config_port(config_port);

    for (const auto& target : targets) {
        const auto answer = answer_to(address(),
                                      request_with("GET", target, "Host: localhost:8080\r\n"));

        EXPECT_TRUE(has_status(answer, "302 Found")) << target << answer;
        EXPECT_TRUE(has_header(answer, "Location", "http://localhost:8180" + target))
            << target << answer;
    }
}

// Whatever else is asked beneath /config on the main port is 404, naming
// where /config is, whatever credential or signature it carries, or none
// (HttpApi §2.3).
TEST_F(ServiceTest, AnswersAnythingElseBeneathConfigWithWhereItIs) {
    const std::vector<std::pair<std::string, std::string>> requests = {
        {"GET", "/config/api"},
        {"GET", "/config/api/"},
        {"GET", "/config/api/node"},
        {"GET", "/config/api?x=1"},
        {"GET", "/config/%61pi/node"},
        {"POST", "/config/api/connections"},
        {"DELETE", "/config/api/applications/wiki"},
        {"POST", "/config/"},
        {"PUT", "/config/index.html"},
    };

    service().set_config_port(config_port);

    for (const auto& [method, target] : requests) {
        const auto answer = answer_to(
            address(), request_with(method, target,
                                    "Host: localhost:8080\r\n"
                                    "Authorization: Basic YWRtaW46c2VjcmV0\r\n"));

        EXPECT_TRUE(has_status(answer, "404 Not Found")) << method << target << answer;
        EXPECT_NE(std::string::npos,
                  body_of(answer).find("/config is served on port 8180, at http://localhost:8180"
                                       + target))
            << method << target << answer;
        EXPECT_TRUE(is_signed_by_node(answer)) << method << target << answer;
        EXPECT_EQ(std::string::npos, answer.find("WWW-Authenticate")) << answer;
    }
}

// The answer to a HEAD has no body, and is signed as having none.
TEST_F(ServiceTest, AnswersAHeadBeneathConfigWithNotFound) {
    service().set_config_port(config_port);

    const auto answer = answer_to(address(), request_of("HEAD", "/config/"));

    EXPECT_TRUE(has_status(answer, "404 Not Found")) << answer;
    EXPECT_TRUE(is_signed_by_node(answer)) << answer;
    EXPECT_EQ(body_of(answer), "");
}

// Told /config's port, it serves what it did elsewhere.
TEST_F(ServiceTest, ServesOtherPathsAsBeforeOnceToldConfigsPort) {
    service().set_config_port(config_port);

    const auto nodes = answer_to(address(), get("/data/nodes"));
    const auto configuration = answer_to(address(), get("/configuration"));

    EXPECT_TRUE(has_status(nodes, "200 OK")) << nodes;
    EXPECT_EQ(body_of(nodes), own_list().to_string());
    EXPECT_TRUE(has_status(configuration, "404 Not Found")) << configuration;
    EXPECT_NE(std::string::npos,
              body_of(configuration).find("No resource exists at this path."))
        << configuration;
}

TEST_F(ServiceTest, RefusesAConfigPortOfZero) {
    EXPECT_THROW(service().set_config_port(0), std::invalid_argument);
}

// What is posted under a list's name that is not one is logged, and the
// lists posted after it are still served.
TEST_F(ServiceTest, LogsWhatIsNotAList) {
    auto nodes = own_list();

    nodes.add(std::string(peer_endpoint), Identifier("sha256", std::string(peer_hash)));
    Logger::reset_levels(Logger::Level::Warning);
    Channel::post(NodesUpdated::name, std::string("not a list"));
    Channel::post(NodesUpdated::name, NodesUpdated{nodes, NodeList()});

    EXPECT_EQ(served_eventually(address(), "/data/nodes", nodes.to_string()), nodes.to_string());
    EXPECT_TRUE(logged("Not a list the web server serves: not a list")) << log_text();
}

// A client that connects and sends nothing does not keep the service from
// stopping.
TEST_F(ServiceTest, StopsWithinFiveSecondsWithAClientConnected) {
    constexpr auto most = std::chrono::seconds(5);
    const auto client = connect_when_listening(address());

    client->send_all("GET /data/");

    const auto stopping = std::chrono::steady_clock::now();

    stop();

    EXPECT_LT(std::chrono::steady_clock::now() - stopping, most);
}

TEST_F(ServiceTest, RefusesASearchCacheTtlOfZero) {
    const fixtures::ScratchDirectory directory;
    const auto store = std::make_shared<fixtures::MemorySource>();
    const NodeIdentity identity(directory.path() / "node-key.pem", *store);
    Settings settings;

    settings.search_cache_ttl = Service::Milliseconds::zero();

    EXPECT_THROW(const Service refused(loopback(), store, identity, settings),
                 std::invalid_argument);
}

TEST_F(ServiceTest, RefusesTooFewProvisionalTrustAttempts) {
    const fixtures::ScratchDirectory directory;
    const auto store = std::make_shared<fixtures::MemorySource>();
    const NodeIdentity identity(directory.path() / "node-key.pem", *store);
    Settings settings;

    settings.provisional_trust_attempts = 1;

    EXPECT_THROW(const Service refused(loopback(), store, identity, settings),
                 std::invalid_argument);
}

// Another service cannot listen where one listens. Linux lets two sockets
// bind one port, as SO_REUSEADDR asks, until one listens.
TEST_F(ServiceTest, RefusesAnAddressInUse) {
    const fixtures::ScratchDirectory directory;
    const auto store = std::make_shared<fixtures::MemorySource>();
    const NodeIdentity identity(directory.path() / "node-key.pem", *store);

    (void)connect_when_listening(address());

    EXPECT_THROW(const Service second(address(), store, identity, Settings{}), std::system_error);
}
