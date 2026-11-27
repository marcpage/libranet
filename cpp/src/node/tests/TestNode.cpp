#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <csignal>
#include <cstddef>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <ios>
#include <iterator>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>
#include <utility>
#include <vector>

#include "logger/ILogOutput.hpp"
#include "logger/Logger.hpp"
#include "net/Address.hpp"
#include "net/ISocket.hpp"
#include "node/Node.hpp"
#include "node/SettingsFile.hpp"

using logger::ILogOutput;
using logger::Logger;
using net::Address;
using net::ISocket;
using node::Node;
using node::SettingsFile;
using std::invalid_argument;
using std::map;
using std::mutex;
using std::optional;
using std::scoped_lock;
using std::shared_ptr;
using std::string;
using std::string_view;
using std::system_error;
using std::vector;
using std::chrono::milliseconds;
using std::chrono::seconds;
using std::chrono::steady_clock;
using std::filesystem::path;
using std::filesystem::perms;

namespace {

// "hello", and its SHA-256 digest, as `shasum -a 256` gives it.
constexpr auto hello = "hello";
constexpr auto hello_hash = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";

// RFC 8032 §7.1 TEST 2's key, as Python's cryptography package writes it,
// with its public key and the SHA-256 of that, its id, as in
// identity/tests/TestNodeIdentity.cpp.
constexpr auto python_private_pem =
    "-----BEGIN PRIVATE KEY-----\n"
    "MC4CAQAwBQYDK2VwBCIEIEzNCJso/5banbbDRuwRTg9bijGfNaumJNqM9u1PuKb7\n"
    "-----END PRIVATE KEY-----\n";
constexpr auto python_public_pem =
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEAPUAXw+hDiVqStwqnTRt+vJyYLM8uxJaMwM1V8Sr0Zgw=\n"
    "-----END PUBLIC KEY-----\n";
constexpr auto python_hash = "bf019c455f05e75ce74ca02a55a4b88bab561f85a76555d8281a79f7c2985233";

// A request for hello, signed as storage asks, by any signature, on a
// connection the node closes once it answers.
string get_hello() {
    return string("GET /data/sha256/") + hello_hash + " HTTP/1.1\r\nConnection: close\r\n\r\n";
}

// How long to wait, at most, for the node to listen, and how long between
// tries. The node binds when it is made, but listens only once its thread
// has started. Until then, Linux refuses a connection to it, and macOS holds
// one until it listens.
constexpr auto listen_timeout = seconds(30);
constexpr auto retry_interval = milliseconds(5);

// How many bytes to read from a connection at a time.
constexpr std::size_t read_size = 4096;

// A directory of the running test's own, made under the system's directory
// for temporary files, and removed with all it holds when this is destroyed.
// Its name holds the test's and a random number, so that two runs of the
// tests at once do not share one.
class ScratchDirectory {
public:
    ScratchDirectory();
    ~ScratchDirectory();

    [[nodiscard]] const path& directory() const noexcept;

    ScratchDirectory(const ScratchDirectory&) = delete;
    ScratchDirectory& operator=(const ScratchDirectory&) = delete;
    ScratchDirectory(ScratchDirectory&&) = delete;
    ScratchDirectory& operator=(ScratchDirectory&&) = delete;

private:
    path m_path;
};

ScratchDirectory::ScratchDirectory() {
    const auto* const test = testing::UnitTest::GetInstance()->current_test_info();
    std::random_device random;

    m_path = std::filesystem::temp_directory_path()
             / (string("libranet-") + test->test_suite_name() + "-" + test->name() + "-"
                + std::to_string(random()));
    std::filesystem::create_directories(m_path);
}

ScratchDirectory::~ScratchDirectory() {
    std::error_code error;

    std::filesystem::remove_all(m_path, error);
}

const path& ScratchDirectory::directory() const noexcept {
    return m_path;
}

// An environment variable set, or unset, as a test needs it, and as it was
// once the test ends. Tests set them before making a node, while no other
// thread runs.
class ScopedVariable {
public:
    ScopedVariable(string name, const optional<string>& value);
    ~ScopedVariable();

    ScopedVariable(const ScopedVariable&) = delete;
    ScopedVariable& operator=(const ScopedVariable&) = delete;
    ScopedVariable(ScopedVariable&&) = delete;
    ScopedVariable& operator=(ScopedVariable&&) = delete;

private:
    string m_name;
    optional<string> m_previous;

    void _set(const optional<string>& value) const;
};

ScopedVariable::ScopedVariable(string name, const optional<string>& value)
    : m_name(std::move(name)) {
    // NOLINTNEXTLINE(concurrency-mt-unsafe)
    if (const char* const previous = std::getenv(m_name.c_str()); nullptr != previous) {
        m_previous = previous;
    }

    _set(value);
}

ScopedVariable::~ScopedVariable() {
    _set(m_previous);
}

void ScopedVariable::_set(const optional<string>& value) const {
    if (value.has_value()) {
        // NOLINTNEXTLINE(concurrency-mt-unsafe)
        (void)::setenv(m_name.c_str(), value->c_str(), 1);
    } else {
        // NOLINTNEXTLINE(concurrency-mt-unsafe)
        (void)::unsetenv(m_name.c_str());
    }
}

// The lines the log writes, from any thread.
struct KeptLines {
    mutex lock;
    vector<string> lines;
};

// Keeps each line it is given.
class KeepingOutput : public ILogOutput {
public:
    explicit KeepingOutput(shared_ptr<KeptLines> kept);

    void log(string_view line) override;

private:
    shared_ptr<KeptLines> m_kept;
};

KeepingOutput::KeepingOutput(shared_ptr<KeptLines> kept)
    : m_kept(std::move(kept)) {}

void KeepingOutput::log(string_view line) {
    const scoped_lock guard(m_kept->lock);

    m_kept->lines.emplace_back(line);
}

// Settings named "section.name", each with its value as YAML writes it.
using Values = map<string, string>;

// The settings that values give, laid out in sections.
SettingsFile settings_of(const Values& values) {
    map<string, string> sections;
    string text;

    for (const auto& [name, value] : values) {
        const auto dot = name.find('.');

        sections[name.substr(0, dot)] += "  " + name.substr(dot + 1) + ": " + value + "\n";
    }

    for (const auto& [section, lines] : sections) {
        text += section + ":\n";
        text += lines;
    }

    return SettingsFile::parse(text);
}

// The settings of a node keeping its data in data_dir, and listening on the
// loopback address, at a port the system picks, with /config at another.
Values loopback(const path& data_dir) {
    return {
        {"storage.data_dir", "'" + data_dir.string() + "'"},
        {"network.listen_address", "127.0.0.1"},
        {"network.listen_port", "0"},
        {"network.config_port", "0"},
    };
}

// A node made with settings and seeds, on the heap. ThreadSanitizer knows a
// lock by its address, and is not told when a std::mutex is destroyed, so it
// would take a lock of a node made on the stack for the lock another node,
// as Node::run() makes one, later has there, and find them taken in an order
// neither is. It is told when memory on the heap is freed.
std::unique_ptr<const Node> node_of(const SettingsFile& settings,
                                    const vector<string>& seeds = {}) {
    return std::make_unique<const Node>(settings, seeds);
}

// What making a node with values and seeds says, when it turns them down as
// unusable, or nothing if it does not.
string refusal(const Values& values, const vector<string>& seeds = {}) {
    try {
        const auto node = node_of(settings_of(values), seeds);
    } catch (const invalid_argument& error) {
        return error.what();
    }

    return "";
}

// The file at file_path, with text in it, and the directories it is in.
void write_file(const path& file_path, string_view text) {
    std::filesystem::create_directories(file_path.parent_path());

    std::ofstream file(file_path, std::ios::binary | std::ios::trunc);

    file << text;
}

// A connection to address, once the node there is listening.
ISocket::Ptr connect_when_listening(const Address& address) {
    const auto deadline = steady_clock::now() + listen_timeout;

    while (true) {
        try {
            return ISocket::connect(address);
        } catch (const system_error&) {
            if (steady_clock::now() >= deadline) {
                throw;
            }
        }

        std::this_thread::sleep_for(retry_interval);
    }
}

// What the node at address answers to request, sent on a connection of its
// own, until it closes the connection.
string answer_to(const Address& address, string_view request) {
    const auto connection = connect_when_listening(address);
    string answer;
    vector<std::byte> buffer(read_size);

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

// Runs a node with values until it is sent signal, which is sent before it
// starts, and held back from this thread until the node takes it.
void run_until(int signal, const Values& values) {
    sigset_t signals{};
    sigset_t previous{};

    (void)sigemptyset(&signals);
    (void)sigaddset(&signals, signal);
    (void)pthread_sigmask(SIG_BLOCK, &signals, &previous);
    (void)pthread_kill(pthread_self(), signal);
    EXPECT_NO_THROW(Node::run(settings_of(values)));
    (void)pthread_sigmask(SIG_SETMASK, &previous, nullptr);
}

// Each test has a directory for the node's data, and keeps what the node
// logs.
class NodeTest : public testing::Test {
protected:
    void SetUp() override {
        Logger::remove_all_outputs();
        Logger::add_output(std::make_unique<KeepingOutput>(m_kept));
    }

    void TearDown() override {
        Logger::remove_all_outputs();
    }

    [[nodiscard]] const path& data_dir() const noexcept {
        return m_directory.directory();
    }

    // Whether a line the log wrote holds text.
    [[nodiscard]] bool logged(string_view text) const {
        const scoped_lock guard(m_kept->lock);

        return std::any_of(m_kept->lines.begin(), m_kept->lines.end(),
                           [text](const string& line) {
            return string::npos != line.find(text);
        });
    }

private:
    ScratchDirectory m_directory;
    shared_ptr<KeptLines> m_kept = std::make_shared<KeptLines>();
};

} // namespace

TEST_F(NodeTest, LaysOutTheDataDirectory) {
    const auto node = node_of(settings_of(loopback(data_dir())));

    EXPECT_TRUE(std::filesystem::is_directory(data_dir() / "cas"));
    EXPECT_TRUE(std::filesystem::is_directory(data_dir() / "incoming"));
}

TEST_F(NodeTest, ListensWhereTheSettingsSay) {
    const auto node = node_of(settings_of(loopback(data_dir())));

    EXPECT_EQ(node->local_address().host(), "127.0.0.1");
    EXPECT_NE(node->local_address().port(), 0);
}

// Its settings, the address it listens on, and each setting it does not use.
TEST_F(NodeTest, LogsItsSettings) {
    auto values = loopback(data_dir());

    values["storage.max_storage_bytes"] = "5000000";
    values["storage.max_decompressed_list_bytes"] = "8388608";
    values["karma.mining"] = "false";

    const auto node = node_of(settings_of(values));

    EXPECT_TRUE(logged("storage.data_dir = " + data_dir().string()));
    EXPECT_TRUE(logged("network.listen_address = 127.0.0.1"));
    EXPECT_TRUE(logged("network.listen_port = 0"));
    EXPECT_TRUE(logged("network.config_port = 0"));
    EXPECT_TRUE(logged("/config is at http://127.0.0.1:"));
    EXPECT_TRUE(logged("logging.level = INFO"));
    EXPECT_TRUE(logged("storage.max_storage_bytes = 5000000"));
    EXPECT_TRUE(logged("storage.max_decompressed_list_bytes = 8388608"));
    EXPECT_TRUE(logged("Seeds: 0"));
    EXPECT_TRUE(logged("Listening on " + node->local_address().to_string()));
    EXPECT_TRUE(logged("Setting not used: karma.mining"));
}

// What it remembers of the network is kept in its database.
TEST_F(NodeTest, KeepsItsDatabase) {
    const auto node = node_of(settings_of(loopback(data_dir())));

    EXPECT_TRUE(std::filesystem::is_regular_file(data_dir() / "libranet.sqlite3"));
}

// The seeds are those the command line names, then those of the seed file.
TEST_F(NodeTest, TakesItsSeedsFromTheCommandLineAndTheSeedFile) {
    const auto seed_file = data_dir() / "seeds.json";
    auto values = loopback(data_dir());

    write_file(seed_file, R"({"nodes": {"http://127.0.0.1:9": null, "http://127.0.0.1:7": )"
                          R"("sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362)"
                          R"(938b9824"}})");
    values["peers.seed_file"] = "'" + seed_file.string() + "'";

    const vector<string> seeds{
        "http://127.0.0.1:8",
        "http://127.0.0.1:6=sha256/" + string(hello_hash),
    };
    const auto node = node_of(settings_of(values), seeds);

    EXPECT_TRUE(logged("Seeds: 4"));
}

// A seed that is not an http URL, or names what is not a node id, is turned
// down, before the node makes anything.
TEST_F(NodeTest, RefusesSeedsItCannotUse) {
    const auto unmade = data_dir() / "unmade";

    for (const auto* const seed : {
             "ftp://127.0.0.1:9",
             "127.0.0.1:9",
             "http://127.0.0.1:9=x",
             "http://127.0.0.1:9=sha256/abc",
         }) {
        EXPECT_NE(refusal(loopback(unmade), {seed}).find("--seed:"), string::npos) << seed;
        EXPECT_FALSE(std::filesystem::exists(unmade)) << seed;
    }
}

// A seed file it cannot read, or that is not a node list of http endpoints
// and node ids, is turned down, before the node makes anything.
TEST_F(NodeTest, RefusesASeedFileItCannotUse) {
    const auto unmade = data_dir() / "unmade";
    const auto not_json = data_dir() / "not.json";
    const auto not_a_list = data_dir() / "not-a-list.json";
    const auto bad_id = data_dir() / "bad-id.json";

    write_file(not_json, "{");
    write_file(not_a_list, R"({"peers": {}})");
    write_file(bad_id, R"({"nodes": {"http://127.0.0.1:9": "sha256/abc"}})");

    for (const auto& file : {
             data_dir() / "missing.json",
             not_json,
             not_a_list,
             bad_id,
         }) {
        auto values = loopback(unmade);

        values["peers.seed_file"] = "'" + file.string() + "'";

        EXPECT_NE(refusal(values).find("peers.seed_file:"), string::npos) << file;
        EXPECT_FALSE(std::filesystem::exists(unmade)) << file;
    }
}

// What cas/ holds is served from /data.
TEST_F(NodeTest, ServesWhatCasHolds) {
    write_file(data_dir() / "cas" / "sha256" / "2cf" / hello_hash, hello);

    const auto node = node_of(settings_of(loopback(data_dir())));
    const auto answer = answer_to(node->local_address(), get_hello());

    EXPECT_EQ(answer.rfind("HTTP/1.1 200 OK\r\n", 0), 0U) << answer;
    EXPECT_EQ(answer.substr(answer.find("\r\n\r\n")), "\r\n\r\nhello");
}

// Its key is made the first time it starts, and only its owner may read it.
TEST_F(NodeTest, MakesItsKeyTheFirstTimeItStarts) {
    const auto node = node_of(settings_of(loopback(data_dir())));

    EXPECT_EQ(std::filesystem::status(data_dir() / "node-key.pem").permissions(),
              perms::owner_read | perms::owner_write);
    EXPECT_TRUE(logged("Made the node's key: " + (data_dir() / "node-key.pem").string()));
    EXPECT_TRUE(logged("Node id: sha256/"));
}

// The key it is given is its own, and its id, which it logs, names its
// public key in /data.
TEST_F(NodeTest, ServesItsPublicKeyUnderItsId) {
    write_file(data_dir() / "node-key.pem", python_private_pem);
    std::filesystem::permissions(data_dir() / "node-key.pem",
                                 perms::owner_read | perms::owner_write);

    const auto node = node_of(settings_of(loopback(data_dir())));
    const auto answer = answer_to(node->local_address(),
                                  string("GET /data/sha256/") + python_hash
                                      + " HTTP/1.1\r\nConnection: close\r\n"
                                        "X-Node-Signature: test\r\n\r\n");

    EXPECT_TRUE(logged(string("Node id: sha256/") + python_hash));
    EXPECT_EQ(answer.rfind("HTTP/1.1 200 OK\r\n", 0), 0U) << answer;
    EXPECT_EQ(answer.substr(answer.find("\r\n\r\n")), string("\r\n\r\n") + python_public_pem);
}

// Each answer it sends is signed with its key, naming its id, and an
// unsigned upload is refused (HandshakeProtocol §2.1).
TEST_F(NodeTest, SignsItsAnswersAndRefusesUnsignedUploads) {
    write_file(data_dir() / "node-key.pem", python_private_pem);
    std::filesystem::permissions(data_dir() / "node-key.pem",
                                 perms::owner_read | perms::owner_write);

    const auto node = node_of(settings_of(loopback(data_dir())));
    const auto answer = answer_to(node->local_address(),
                                  string("PUT /data/sha256/") + hello_hash
                                      + " HTTP/1.1\r\nConnection: close\r\n"
                                        "Content-Length: 5\r\n\r\nhello");

    EXPECT_EQ(answer.rfind("HTTP/1.1 401 Unauthorized\r\n", 0), 0U) << answer;
    EXPECT_NE(answer.find(string(";keyid=\"sha256/") + python_hash + "\""), string::npos)
        << answer;
    EXPECT_FALSE(std::filesystem::exists(data_dir() / "cas" / "sha256" / "2cf" / hello_hash));
}

// A body larger than storage.max_object_bytes is refused unread.
TEST_F(NodeTest, RefusesBodiesLargerThanItsSetting) {
    auto values = loopback(data_dir());

    values["storage.max_object_bytes"] = "4";

    const auto node = node_of(settings_of(values));
    const auto answer = answer_to(node->local_address(),
                                  string("PUT /data/sha256/") + hello_hash
                                      + " HTTP/1.1\r\nConnection: close\r\n"
                                        "Content-Length: 5\r\n\r\nhello");

    EXPECT_EQ(answer.rfind("HTTP/1.1 413 Payload Too Large\r\n", 0), 0U) << answer;
    EXPECT_TRUE(logged("storage.max_object_bytes = 4"));
}

// Its node list is served in fewer bytes than stats.max_list_bytes, leaving
// out what does not fit.
TEST_F(NodeTest, ServesItsNodeListWithinItsSetting) {
    auto values = loopback(data_dir());

    values["stats.max_list_bytes"] = "64";

    const auto node = node_of(settings_of(values));
    const auto answer = answer_to(node->local_address(),
                                  "GET /data/nodes HTTP/1.1\r\nConnection: close\r\n\r\n");

    EXPECT_EQ(answer.rfind("HTTP/1.1 200 OK\r\n", 0), 0U) << answer;
    EXPECT_EQ(answer.substr(answer.find("\r\n\r\n")), "\r\n\r\n{\"nodes\":{}}");
}

// Its node list names it at localhost, the port it listens on, until it is
// given another (HttpApi §10.1), and is signed as each answer is.
TEST_F(NodeTest, ServesItsOwnEndpointAsItsNodeList) {
    write_file(data_dir() / "node-key.pem", python_private_pem);
    std::filesystem::permissions(data_dir() / "node-key.pem",
                                 perms::owner_read | perms::owner_write);

    const auto node = node_of(settings_of(loopback(data_dir())));
    const auto answer = answer_to(node->local_address(),
                                  "GET /data/nodes HTTP/1.1\r\nConnection: close\r\n\r\n");
    const auto port = std::to_string(node->local_address().port());

    EXPECT_EQ(answer.rfind("HTTP/1.1 200 OK\r\n", 0), 0U) << answer;
    EXPECT_NE(answer.find(string(";keyid=\"sha256/") + python_hash + "\""), string::npos)
        << answer;
    EXPECT_EQ(answer.substr(answer.find("\r\n\r\n")),
              "\r\n\r\n{\"nodes\":{\"http://localhost:" + port + "\":\"sha256/" + python_hash
                  + "\"}}");
}

// /config is served on a port of its own, which a GET of /config/ on the main
// port is sent to, and behind a credential, which the first request captures
// in the data directory.
TEST_F(NodeTest, ServesConfigOnAPortOfItsOwn) {
    const auto node = node_of(settings_of(loopback(data_dir())));
    const auto moved = answer_to(node->local_address(),
                                 "GET /config/ HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                                 "Connection: close\r\n\r\n");
    const string location_field = "\r\nLocation: http://127.0.0.1:";
    const auto location = moved.find(location_field);

    ASSERT_EQ(moved.rfind("HTTP/1.1 302 Found\r\n", 0), 0U) << moved;
    ASSERT_NE(location, string::npos) << moved;

    const auto port_start = location + location_field.size();
    const auto port = moved.substr(port_start, moved.find('/', port_start) - port_start);
    const Address config("127.0.0.1", static_cast<Address::Port>(std::stoul(port)));

    EXPECT_NE(config.port(), node->local_address().port());
    EXPECT_NE(moved.find("\r\nLocation: http://127.0.0.1:" + port + "/config/\r\n"),
              string::npos)
        << moved;
    EXPECT_TRUE(logged("/config is at http://127.0.0.1:" + port + "/config/"));

    const auto answer = answer_to(config,
                                  "GET /config/api HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                                  "Authorization: Basic YWRtaW46c2VjcmV0\r\n"
                                  "Connection: close\r\n\r\n");

    EXPECT_EQ(answer.rfind("HTTP/1.1 200 OK\r\n", 0), 0U) << answer;
    EXPECT_TRUE(std::filesystem::is_regular_file(data_dir() / "config-credential"));
}

// /config says what the node is: the address its main port was set to listen
// at, with the port it took, and the endpoint it advertises.
TEST_F(NodeTest, SaysWhatItIsOnConfig) {
    const auto node = node_of(settings_of(loopback(data_dir())));
    const auto moved = answer_to(node->local_address(),
                                 "GET /config/ HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                                 "Connection: close\r\n\r\n");
    const string location_field = "\r\nLocation: http://127.0.0.1:";
    const auto location = moved.find(location_field);

    ASSERT_NE(location, string::npos) << moved;

    const auto port_start = location + location_field.size();
    const auto port = moved.substr(port_start, moved.find('/', port_start) - port_start);
    const Address config("127.0.0.1", static_cast<Address::Port>(std::stoul(port)));
    const auto main_port = std::to_string(node->local_address().port());
    const auto answer = answer_to(config,
                                  "GET /config/api/node HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                                  "Authorization: Basic YWRtaW46c2VjcmV0\r\n"
                                  "Connection: close\r\n\r\n");

    EXPECT_EQ(answer.rfind("HTTP/1.1 200 OK\r\n", 0), 0U) << answer;
    EXPECT_NE(answer.find(R"("listen_address":"127.0.0.1")"), string::npos) << answer;
    EXPECT_NE(answer.find(R"("listen_port":)" + main_port + ","), string::npos) << answer;
    EXPECT_NE(answer.find(R"("advertised_endpoint":"http://localhost:)" + main_port + "\""),
              string::npos)
        << answer;
}

// network.config_port may be neither the main port nor, when it is not set,
// left with no port above the main port to take, which is turned down before
// the node makes anything.
TEST_F(NodeTest, RefusesAConfigPortItCannotUse) {
    const auto unmade = data_dir() / "unmade";
    auto same = loopback(unmade);
    auto none_left = loopback(unmade);

    same["network.listen_port"] = "8080";
    same["network.config_port"] = "8080";
    none_left["network.listen_port"] = "65500";
    none_left.erase("network.config_port");

    EXPECT_NE(refusal(same).find("network.config_port: must not be network.listen_port"),
              string::npos);
    EXPECT_NE(refusal(none_left).find("network.config_port: must be set"), string::npos);
    EXPECT_FALSE(std::filesystem::exists(unmade));
}

// What each directory in incoming/ holds is served from /data too.
TEST_F(NodeTest, ServesWhatIncomingHolds) {
    write_file(data_dir() / "incoming" / "batch" / "sha256" / "2cf" / hello_hash, hello);

    const auto node = node_of(settings_of(loopback(data_dir())));
    const auto answer = answer_to(node->local_address(), get_hello());

    EXPECT_EQ(answer.rfind("HTTP/1.1 200 OK\r\n", 0), 0U) << answer;
    EXPECT_EQ(answer.substr(answer.find("\r\n\r\n")), "\r\n\r\nhello");
}

// A client that connects and sends nothing does not keep the node from
// stopping.
TEST_F(NodeTest, StopsWithinFiveSecondsWithAClientConnected) {
    constexpr auto most = seconds(5);
    auto node = std::make_unique<Node>(settings_of(loopback(data_dir())));
    const auto client = connect_when_listening(node->local_address());

    client->send_all("GET /data/sha256/");

    const auto stopping = steady_clock::now();

    node.reset();

    EXPECT_LT(steady_clock::now() - stopping, most);
}

TEST_F(NodeTest, RunStopsOnSigint) {
    run_until(SIGINT, loopback(data_dir()));

    EXPECT_TRUE(std::filesystem::is_directory(data_dir() / "cas"));
    EXPECT_TRUE(logged("Stopping on SIGINT"));
}

TEST_F(NodeTest, RunStopsOnSigterm) {
    run_until(SIGTERM, loopback(data_dir()));

    EXPECT_TRUE(logged("Stopping on SIGTERM"));
}

TEST_F(NodeTest, LogLevels) {
    for (const auto* const level : {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}) {
        auto values = loopback(data_dir());

        values["logging.level"] = level;

        EXPECT_EQ(refusal(values), "") << level;
    }
}

// Each setting it cannot use is turned down, by name, before the node makes
// anything.
TEST_F(NodeTest, RefusesSettingsItCannotUse) {
    const auto unmade = data_dir() / "unmade";
    const vector<std::pair<string, string>> unusable = {
        {"network.listen_port", "x"},
        {"network.listen_port", "65536"},
        {"network.listen_port", "-1"},
        {"network.listen_port", "80.0"},
        {"network.listen_port", "123456"},
        {"network.listen_port", "''"},
        {"network.listen_port", "[80, 81]"},
        {"network.config_port", "x"},
        {"network.config_port", "65536"},
        {"network.config_port", "-1"},
        {"network.listen_address", "[a, b]"},
        {"logging.level", "LOUD"},
        {"logging.level", "info"},
        {"storage.data_dir", "''"},
        {"network.retry_after_seconds", "1.5"},
        {"network.retry_after_seconds", "-1"},
        {"peers.min_outgoing_connections", "0"},
        {"peers.min_outgoing_connections", "17"},
        {"peers.bucket_prefix_bits", "17"},
        {"peers.min_neighborhood_connections", "17"},
        {"peers.neighborhood_prefix_bits", "0"},
        {"peers.connect_timeout_seconds", "0"},
        {"peers.request_timeout_seconds", "x"},
        {"peers.retry_delay_seconds", "-1"},
        {"peers.turned_away_rest_seconds", ".nan"},
        {"peers.seek_refresh_seconds", "60"},
        {"peers.search_passes", "1"},
        {"peers.search_relays", "-1"},
        {"peers.failed_search_hold_seconds", "10"},
        {"peers.broadcast_hold_seconds", "0.0001"},
        {"peers.search_workers", "0"},
        {"peers.pipeline_depth", "0"},
        {"peers.seed_file", "''"},
        {"storage.min_free_bytes", "-1"},
        {"storage.max_storage_bytes", "99999999999999999999999"},
        {"storage.max_hand_offs", "0"},
        {"storage.candidates_timeout_seconds", "0"},
        {"storage.max_candidates", "0"},
        {"storage.stored_grace_seconds", "-1"},
        {"storage.search_cache_ttl_seconds", "0"},
        {"storage.search_min_bits", "257"},
        {"storage.max_decompressed_list_bytes", "0"},
        {"storage.max_object_bytes", "0"},
        {"storage.max_object_bytes", "1048577"},
        {"identity.provisional_trust_attempts", "1"},
        {"identity.signature_max_age_seconds", "0"},
        {"identity.signature_clock_skew_seconds", "-1"},
        {"stats.derive_interval_seconds", "1e10"},
        {"stats.max_list_bytes", "63"},
        {"stats.max_list_bytes", "1048577"},
        {"stats.seek_entry_ttl_seconds", "0"},
        {"stats.max_address_failures", "0"},
        {"stats.max_node_failures", "0"},
    };

    for (const auto& [name, value] : unusable) {
        auto values = loopback(unmade);

        values[name] = value;

        EXPECT_NE(refusal(values).find(name + ":"), string::npos) << name << ": " << value;
        EXPECT_FALSE(std::filesystem::exists(unmade)) << name << ": " << value;
    }
}

// Without storage.data_dir, the node keeps its data where the Python node
// does.
TEST_F(NodeTest, DefaultDataDirectory) {
    const ScopedVariable home("HOME", data_dir().string());
    const ScopedVariable data_home("XDG_DATA_HOME", std::nullopt);
    auto values = loopback(data_dir());

    values.erase("storage.data_dir");

    const auto node = node_of(settings_of(values));

#if __APPLE__
    EXPECT_TRUE(std::filesystem::is_directory(data_dir() / "Library" / "Application Support"
                                              / "libranet" / "cas"));
#else
    EXPECT_TRUE(std::filesystem::is_directory(data_dir() / ".local" / "share" / "libranet"
                                              / "cas"));
#endif
}

#if !__APPLE__
TEST_F(NodeTest, DefaultDataDirectoryInXdgDataHome) {
    const ScopedVariable data_home("XDG_DATA_HOME", (data_dir() / "data").string());
    auto values = loopback(data_dir());

    values.erase("storage.data_dir");

    const auto node = node_of(settings_of(values));

    EXPECT_TRUE(std::filesystem::is_directory(data_dir() / "data" / "libranet" / "cas"));
}
#endif

TEST_F(NodeTest, NoDefaultDataDirectoryWithoutHome) {
    const ScopedVariable home("HOME", std::nullopt);
    const ScopedVariable data_home("XDG_DATA_HOME", std::nullopt);
    auto values = loopback(data_dir());

    values.erase("storage.data_dir");

    EXPECT_NE(refusal(values).find("storage.data_dir:"), string::npos);
}

// A port another node listens on cannot be listened on. Linux lets two
// sockets bind one port, as SO_REUSEADDR asks, until one listens.
TEST_F(NodeTest, PortInUse) {
    const Node first(settings_of(loopback(data_dir())));
    auto values = loopback(data_dir());

    (void)connect_when_listening(first.local_address());
    values["network.listen_port"] = std::to_string(first.local_address().port());

    EXPECT_THROW(const Node second(settings_of(values)), system_error);
}
