#include "Node.hpp"

#include <algorithm>
#include <array>
#include <cerrno>
#include <csignal>
#include <cstddef>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <functional>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <thread>

#include "logger/Logger.hpp"
#include "net/Address.hpp"
#include "node/SettingsFile.hpp"
#include "storage/Manager.hpp"

using logger::Logger;
using net::Address;
using node::Node;
using node::SettingsFile;
using std::array;
using std::domain_error;
using std::exception;
using std::invalid_argument;
using std::string;
using std::string_view;
using std::system_error;
using std::thread;
using std::filesystem::path;
using storage::Manager;

namespace {

// The settings the node uses, as the Python node names them.
constexpr auto data_dir_setting = "storage.data_dir";
constexpr auto listen_address_setting = "network.listen_address";
constexpr auto listen_port_setting = "network.listen_port";
constexpr auto log_level_setting = "logging.level";
constexpr array used_settings{
    data_dir_setting,
    listen_address_setting,
    listen_port_setting,
    log_level_setting,
};

// The Python node's defaults: every address, IPv6 and IPv4; port 8080; and
// lines at INFO and more severe.
constexpr auto default_listen_address = "::";
constexpr auto default_listen_port = "8080";
constexpr auto default_log_level = "INFO";

// The connections the system queues while the web server answers one.
constexpr int backlog = 128;

// The value of the setting name, or fallback when the settings do not give
// it.
string text(const SettingsFile& settings, const string& name, const string& fallback) {
    if (!settings.has(name)) {
        return fallback;
    }

    try {
        return settings.value(name);
    } catch (const domain_error&) {
        throw invalid_argument(name + ": must be one value, not a list");
    }
}

Logger::Level log_level(const SettingsFile& settings) {
    const auto level = text(settings, log_level_setting, default_log_level);

    if ("CRITICAL" == level) {
        return Logger::Level::Fatal;
    }

    if ("ERROR" == level) {
        return Logger::Level::Error;
    }

    if ("WARNING" == level) {
        return Logger::Level::Warning;
    }

    if ("INFO" == level) {
        return Logger::Level::Info;
    }

    if ("DEBUG" == level) {
        return Logger::Level::Debug;
    }

    throw invalid_argument(string(log_level_setting)
                           + ": must be CRITICAL, ERROR, WARNING, INFO, or DEBUG, not '" + level
                           + "'");
}

// The value of the environment variable name, or "" if it is not set.
string environment(const char* name) {
    // Read before the node starts a thread, and never set by the node.
    // NOLINTNEXTLINE(concurrency-mt-unsafe)
    const char* const value = std::getenv(name);

    return nullptr == value ? "" : value;
}

// The user's data directory for libranet, as the Python node finds it with
// platformdirs.
path default_data_dir() {
    const auto home = environment("HOME");

#ifndef __APPLE__
    if (const auto data_home = environment("XDG_DATA_HOME"); !data_home.empty()) {
        return path(data_home) / "libranet";
    }
#endif

    if (home.empty()) {
        throw invalid_argument(string(data_dir_setting)
                               + ": must be given, as HOME is not set to find the default");
    }

#ifdef __APPLE__
    return path(home) / "Library" / "Application Support" / "libranet";
#else
    return path(home) / ".local" / "share" / "libranet";
#endif
}

path data_dir(const SettingsFile& settings) {
    if (!settings.has(data_dir_setting)) {
        return default_data_dir();
    }

    auto directory = text(settings, data_dir_setting, "");

    if (directory.empty()) {
        throw invalid_argument(string(data_dir_setting) + ": must not be empty");
    }

    return directory;
}

// A whole number from 0, for a port the system picks, to 65535.
Address::Port listen_port(const SettingsFile& settings) {
    constexpr std::size_t most_digits = 5;
    const auto port = text(settings, listen_port_setting, default_listen_port);

    if (!port.empty() && port.size() <= most_digits
        && string::npos == port.find_first_not_of("0123456789")) {
        if (const auto number = std::stoul(port);
            number <= std::numeric_limits<Address::Port>::max()) {
            return static_cast<Address::Port>(number);
        }
    }

    throw invalid_argument(string(listen_port_setting)
                           + ": must be a whole number from 0 to 65535, not '" + port + "'");
}

Address listen_address(const SettingsFile& settings) {
    return {text(settings, listen_address_setting, default_listen_address),
            listen_port(settings)};
}

// The data directory's cas/, where content is stored, and incoming/, made if
// they are not there, with each directory in incoming/ read too.
Manager::Ptr lay_out(const path& directory) {
    const auto cas = directory / "cas";
    const auto incoming = directory / "incoming";
    auto store = Manager::create();

    std::filesystem::create_directories(cas);
    std::filesystem::create_directories(incoming);
    store->add(cas);
    store->scan(incoming);
    return store;
}

// Each setting the file gives that the node does not use, as a mistyped
// name would be.
void warn_of_unused(const SettingsFile& settings) {
    for (const auto& name : settings.names()) {
        if (std::find(used_settings.begin(), used_settings.end(), name) == used_settings.end()) {
            lWarn << "Setting not used:" << name;
        }
    }
}

// SIGINT and SIGTERM, held back from the thread that makes this, and from
// each thread it then starts, until this is destroyed, for wait() to take.
class StopSignals {
public:
    StopSignals();
    ~StopSignals();
    StopSignals(const StopSignals&) = delete;
    StopSignals& operator=(const StopSignals&) = delete;
    StopSignals(StopSignals&&) = delete;
    StopSignals& operator=(StopSignals&&) = delete;

    // Waits for one of them, and says which.
    [[nodiscard]] string_view wait() const;

private:
    sigset_t m_signals{};
    sigset_t m_previous{};
};

StopSignals::StopSignals() {
    (void)sigemptyset(&m_signals);
    (void)sigaddset(&m_signals, SIGINT);
    (void)sigaddset(&m_signals, SIGTERM);

    if (const auto error = pthread_sigmask(SIG_BLOCK, &m_signals, &m_previous); 0 != error) {
        throw system_error(error, std::generic_category(), "pthread_sigmask");
    }
}

StopSignals::~StopSignals() {
    // A stop signal sent again while the node stopped is taken now, as it
    // would have been without this.
    (void)pthread_sigmask(SIG_SETMASK, &m_previous, nullptr);
}

string_view StopSignals::wait() const {
    int taken = 0;
    int error = 0;

    do {
        error = sigwait(&m_signals, &taken);
    } while (EINTR == error);

    if (0 != error) {
        throw system_error(error, std::generic_category(), "sigwait");
    }

    return SIGINT == taken ? "SIGINT" : "SIGTERM";
}

} // namespace

namespace node {

void Node::run(const SettingsFile& settings) {
    const StopSignals stop_signals;
    const Node node(settings);
    const auto signal = stop_signals.wait();

    lInfo << "Stopping on" << signal;
}

Node::Node(const SettingsFile& settings)
    : m_log_level(log_level(settings))
    , m_data_dir(data_dir(settings))
    , m_listen_address(listen_address(settings))
    , m_store(lay_out(m_data_dir))
    , m_data_handler(m_store)
    , m_server(m_listen_address)
    , m_local_address(m_server.local_address()) {
    Logger::reset_levels(m_log_level);
    lInfo << data_dir_setting << "=" << m_data_dir.string();
    lInfo << listen_address_setting << "=" << m_listen_address.host();
    lInfo << listen_port_setting << "=" << m_listen_address.port();
    lInfo << log_level_setting << "=" << text(settings, log_level_setting, default_log_level);
    warn_of_unused(settings);
    lInfo << "Listening on" << m_local_address.to_string();

    m_server.register_handler("/data", std::cref(m_data_handler));
    m_server_thread = thread([this] {
        _serve();
    });
}

Node::~Node() {
    // A thread that cannot be stopped would outlive this object, so then
    // nothing is safe but to end the program, as std::thread's destructor
    // would.
    try {
        m_server.stop();
        m_server_thread.join();
    } catch (...) {
        std::terminate();
    }
}

const Address& Node::local_address() const noexcept {
    return m_local_address;
}

void Node::_serve() {
    try {
        m_server.start(backlog);
    } catch (const exception& error) {
        lErr << "The web server stopped:" << error;
    }
}

} // namespace node
