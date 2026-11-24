#include "Node.hpp"

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <fstream>
#include <ios>
#include <iterator>
#include <limits>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#include "api/Service.hpp"
#include "eviction/Evictor.hpp"
#include "http/Url.hpp"
#include "identity/KnownKeys.hpp"
#include "identity/MessageVerifier.hpp"
#include "identity/NodeIdentity.hpp"
#include "io/Directories.hpp"
#include "logger/Logger.hpp"
#include "net/Address.hpp"
#include "node/SettingsFile.hpp"
#include "peer/Connections.hpp"
#include "peer/Fetch.hpp"
#include "peer/HandOff.hpp"
#include "peer/Pusher.hpp"
#include "peer/Seeker.hpp"
#include "stats/Recorder.hpp"
#include "storage/Identifier.hpp"
#include "storage/Manager.hpp"
#include "types/Converter.hpp"
#include "types/Json.hpp"

using api::Service;
using eviction::Evictor;
using http::Url;
using identity::KnownKeys;
using identity::MessageVerifier;
using io::Directories;
using logger::Logger;
using net::Address;
using node::Node;
using node::SettingsFile;
using peer::Connections;
using peer::Fetch;
using std::array;
using std::domain_error;
using std::exception;
using std::find;
using std::generic_category;
using std::invalid_argument;
using std::make_shared;
using std::numeric_limits;
using std::out_of_range;
using std::size_t;
using std::stoul;
using std::string;
using std::string_view;
using std::system_error;
using std::to_string;
using std::uintmax_t;
using std::vector;
using std::filesystem::create_directories;
using std::filesystem::path;
using storage::Identifier;
using storage::Manager;
using types::Converter;
using types::Json;

namespace {

using Milliseconds = std::chrono::milliseconds;
using Seed = Connections::Seed;

// The settings the node uses, as the Python node names them.
constexpr auto data_dir_setting = "storage.data_dir";
constexpr auto listen_address_setting = "network.listen_address";
constexpr auto listen_port_setting = "network.listen_port";
constexpr auto log_level_setting = "logging.level";
constexpr auto retry_after_setting = "network.retry_after_seconds";
constexpr auto min_outgoing_setting = "peers.min_outgoing_connections";
constexpr auto bucket_bits_setting = "peers.bucket_prefix_bits";
constexpr auto min_neighborhood_setting = "peers.min_neighborhood_connections";
constexpr auto neighborhood_bits_setting = "peers.neighborhood_prefix_bits";
constexpr auto connect_timeout_setting = "peers.connect_timeout_seconds";
constexpr auto request_timeout_setting = "peers.request_timeout_seconds";
constexpr auto retry_delay_setting = "peers.retry_delay_seconds";
constexpr auto turned_away_setting = "peers.turned_away_rest_seconds";
constexpr auto seek_refresh_setting = "peers.seek_refresh_seconds";
constexpr auto search_passes_setting = "peers.search_passes";
constexpr auto search_relays_setting = "peers.search_relays";
constexpr auto failed_hold_setting = "peers.failed_search_hold_seconds";
constexpr auto broadcast_hold_setting = "peers.broadcast_hold_seconds";
constexpr auto search_workers_setting = "peers.search_workers";
constexpr auto pipeline_depth_setting = "peers.pipeline_depth";
constexpr auto seed_file_setting = "peers.seed_file";
constexpr auto min_free_setting = "storage.min_free_bytes";
constexpr auto max_storage_setting = "storage.max_storage_bytes";
constexpr auto max_hand_offs_setting = "storage.max_hand_offs";
constexpr auto candidates_timeout_setting = "storage.candidates_timeout_seconds";
constexpr auto max_candidates_setting = "storage.max_candidates";
constexpr auto stored_grace_setting = "storage.stored_grace_seconds";
constexpr auto search_ttl_setting = "storage.search_cache_ttl_seconds";
constexpr auto search_min_bits_setting = "storage.search_min_bits";
constexpr auto trust_attempts_setting = "identity.provisional_trust_attempts";
constexpr auto max_age_setting = "identity.signature_max_age_seconds";
constexpr auto clock_skew_setting = "identity.signature_clock_skew_seconds";
constexpr auto derive_interval_setting = "stats.derive_interval_seconds";
constexpr auto seek_ttl_setting = "stats.seek_entry_ttl_seconds";
constexpr auto address_failures_setting = "stats.max_address_failures";
constexpr auto node_failures_setting = "stats.max_node_failures";
constexpr array used_settings{
    data_dir_setting,
    listen_address_setting,
    listen_port_setting,
    log_level_setting,
    retry_after_setting,
    min_outgoing_setting,
    bucket_bits_setting,
    min_neighborhood_setting,
    neighborhood_bits_setting,
    connect_timeout_setting,
    request_timeout_setting,
    retry_delay_setting,
    turned_away_setting,
    seek_refresh_setting,
    search_passes_setting,
    search_relays_setting,
    failed_hold_setting,
    broadcast_hold_setting,
    search_workers_setting,
    pipeline_depth_setting,
    seed_file_setting,
    min_free_setting,
    max_storage_setting,
    max_hand_offs_setting,
    candidates_timeout_setting,
    max_candidates_setting,
    stored_grace_setting,
    search_ttl_setting,
    search_min_bits_setting,
    trust_attempts_setting,
    max_age_setting,
    clock_skew_setting,
    derive_interval_setting,
    seek_ttl_setting,
    address_failures_setting,
    node_failures_setting,
};

// The settings logged whether the file gives them or not.
constexpr array always_logged{
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

// The files in the data directory that hold the node's key, and what the
// recorder remembers.
constexpr auto key_file_name = "node-key.pem";
constexpr auto database_file_name = "libranet.sqlite3";

// The most bits a search may need to match, as the Python node's
// TARGET_BITS: those of a SHA-256 hash.
constexpr size_t most_search_bits = 256;

// The most bits a bucket's prefix may have, as the Python node allows.
constexpr size_t most_prefix_bits = 16;

// How long a peer's connection may be idle before the web server, the
// Python node's, or this node's, closes it: a node's seek list must be read
// more often than that, to keep it open (web::Server::default_idle).
constexpr Milliseconds idle_timeout{60'000};

// The longest a duration may be, in seconds: far longer than any setting
// needs, and short enough that no clock overflows reaching it.
constexpr double most_seconds = 1e9;
constexpr double milliseconds_per_second = 1000.0;

// The option of the command line that names seeds.
constexpr auto seed_option = "--seed";

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

// The setting name, which the settings give, as a whole number from least
// to most.
uintmax_t whole_given(const SettingsFile& settings, const string& name, uintmax_t least,
                      uintmax_t most) {
    const auto value = text(settings, name, "");
    const auto refused = [&name, &value, least, most] {
        return invalid_argument(name + ": must be a whole number from " + to_string(least)
                                + " to " + to_string(most) + ", not '" + value + "'");
    };

    if (value.empty() || string::npos != value.find_first_not_of("0123456789")) {
        throw refused();
    }

    uintmax_t number = 0;

    try {
        number = std::stoull(value);
    } catch (const out_of_range&) {
        throw refused();
    }

    if (number < least || number > most) {
        throw refused();
    }

    return number;
}

// The setting name as a whole number from least to most, or fallback when
// the settings do not give it.
template<typename Number>
Number whole(const SettingsFile& settings, const string& name, Number fallback, uintmax_t least,
             uintmax_t most = static_cast<uintmax_t>(numeric_limits<Number>::max())) {
    if (!settings.has(name)) {
        return fallback;
    }

    return static_cast<Number>(whole_given(settings, name, least, most));
}

// Whether a duration may be zero, or must be more.
enum class Zero : std::uint8_t {
    Allowed,
    Refused,
};

// The setting name, a number of seconds, which may have a fraction, to the
// millisecond; or fallback when the settings do not give it.
Milliseconds seconds(const SettingsFile& settings, const string& name, Milliseconds fallback,
                     Zero zero = Zero::Refused) {
    if (!settings.has(name)) {
        return fallback;
    }

    const auto value = text(settings, name, "");
    const auto* const least = Zero::Allowed == zero ? "0" : "more than 0";
    const auto refused = [&name, &value, least] {
        return invalid_argument(name + ": must be a number of seconds, " + least + ", not '"
                                + value + "'");
    };
    double number = 0.0;
    size_t used = 0;

    try {
        number = std::stod(value, &used);
    } catch (const exception&) {
        throw refused();
    }

    if (used != value.size() || !std::isfinite(number) || number < 0.0 || number > most_seconds
        || (Zero::Refused == zero && number <= 0.0)) {
        throw refused();
    }

    const Milliseconds duration(
        static_cast<Milliseconds::rep>(std::llround(number * milliseconds_per_second)));

    if (Zero::Refused == zero && duration <= Milliseconds::zero()) {
        throw invalid_argument(name + ": must be at least 0.001 seconds, not '" + value + "'");
    }

    return duration;
}

// Throws invalid_argument, naming setting, unless there are buckets enough
// for connections, as prefix_bits has.
void check_buckets(const char* setting, size_t connections, size_t prefix_bits) {
    const auto buckets = size_t{1} << prefix_bits;

    if (connections > buckets) {
        throw invalid_argument(string(setting) + ": must be no more than the " + to_string(buckets)
                               + " buckets of " + to_string(prefix_bits) + " bits, not "
                               + to_string(connections));
    }
}

// Throws invalid_argument, naming setting, unless hold outlasts a search,
// which lasts a retry after for each pass after the first, as the peers'
// searches last on a network whose nodes are set alike (High-Level Design
// §4.7).
void check_outlasts_searches(const char* setting, Milliseconds hold, size_t passes,
                             Milliseconds retry_after) {
    const auto longest = retry_after * static_cast<Milliseconds::rep>(passes - 1);

    if (hold <= longest) {
        throw invalid_argument(string(setting) + ": must be longer than the "
                               + to_string(longest.count()) + " ms a search may last: "
                               + search_passes_setting + " less one, times "
                               + retry_after_setting);
    }
}

// Throws invalid_argument, naming source, unless endpoint is an http URL
// naming a host, as the node dials only http (HttpApi §3.1).
void check_endpoint(const string& endpoint, const string& source) {
    const auto url = Url::parse(endpoint);

    if (!url || !url->effective_port() || "http" != Converter::lower_case(url->scheme())
        || url->host().empty()) {
        throw invalid_argument(source + ": '" + endpoint + "' is not an http endpoint");
    }
}

// The node id named, as "{algorithm}/{hash}". Throws invalid_argument,
// naming source, for what names none.
Identifier node_id_of(const string& named, const string& source) {
    auto node_id = Identifier::from(Json(named));

    if (!node_id) {
        throw invalid_argument(source + ": '" + named + "' is not a node id");
    }

    return *node_id;
}

// The seed the command line names as URL[=ID].
Seed seed_of(const string& given) {
    const auto equals = given.rfind('=');
    Seed seed{given.substr(0, equals)};

    check_endpoint(seed.endpoint, seed_option);

    if (string::npos != equals) {
        seed.node_id = node_id_of(given.substr(equals + 1), seed_option);
    }

    return seed;
}

// The seeds the seed list at file names, laid out as the node list is, with
// null for a node id that is not known.
vector<Seed> seeds_in(const path& file) {
    const auto* const source = seed_file_setting;
    std::ifstream stream(file, std::ios::binary);

    if (!stream) {
        throw invalid_argument(string(source) + ": cannot read " + file.string());
    }

    const string contents((std::istreambuf_iterator<char>(stream)),
                          std::istreambuf_iterator<char>());
    Json document;

    try {
        document = Json::parse(contents);
    } catch (const invalid_argument& error) {
        throw invalid_argument(string(source) + ": " + file.string()
                               + " is not JSON: " + error.what());
    }

    if (!document.is_object() || !document.has("nodes") || !document.at("nodes").is_object()) {
        throw invalid_argument(string(source) + ": " + file.string()
                               + " does not name its nodes as a node list does");
    }

    vector<Seed> seeds;

    for (const auto& [endpoint, node_id] : document.at("nodes").object()) {
        Seed seed{endpoint};

        check_endpoint(endpoint, source);

        if (!node_id.is_null()) {
            if (!node_id.is_string()) {
                throw invalid_argument(string(source) + ": the node id of '" + endpoint
                                       + "' is neither text nor null");
            }

            seed.node_id = node_id_of(node_id.string(), source);
        }

        seeds.push_back(std::move(seed));
    }

    return seeds;
}

// The seeds the command line names, then those of the seed file, if the
// settings name one.
vector<Seed> seeds_of(const SettingsFile& settings, const vector<string>& given) {
    vector<Seed> seeds;

    std::transform(given.begin(), given.end(), std::back_inserter(seeds), &seed_of);

    if (settings.has(seed_file_setting)) {
        const auto file = text(settings, seed_file_setting, "");

        if (file.empty()) {
            throw invalid_argument(string(seed_file_setting) + ": must not be empty");
        }

        const auto listed = seeds_in(file);

        seeds.insert(seeds.end(), listed.begin(), listed.end());
    }

    return seeds;
}

// The settings with the web server asking fetch whether it looks for content
// it does not hold.
Service::Settings looking_with(Service::Settings settings, const Fetch& fetch) {
    settings.is_held_off = [&fetch](const Identifier& identifier) {
        return fetch.is_held_off(identifier);
    };
    return settings;
}

// Gives the sessions with the nodes connections has connected to, as they
// are when asked.
Fetch::GetPeers sessions_of(const Connections& connections) {
    return [&connections] { return connections.open_sessions(); };
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

// The log level the settings give, set for the log before the node makes
// anything, so that what its parts log as they are made is shown.
Logger::Level applied_log_level(const SettingsFile& settings) {
    const auto level = log_level(settings);

    Logger::reset_levels(level);
    return level;
}

// The user's data directory for libranet, as the Python node finds it with
// platformdirs.
path default_data_dir() {
    const auto data_dir = Directories::user_app_data();

    if (!data_dir) {
        throw invalid_argument(string(data_dir_setting)
                               + ": must be given, as HOME is not set to find the default");
    }

    return *data_dir / "libranet";
}

path data_dir(const SettingsFile& settings) {
    auto directory = text(settings, data_dir_setting, default_data_dir());

    if (directory.empty()) {
        throw invalid_argument(string(data_dir_setting) + ": must not be empty");
    }

    return directory;
}

// A whole number from 0, for a port the system picks, to 65535.
Address::Port listen_port(const SettingsFile& settings) {
    constexpr size_t most_digits = 5;
    const auto port = text(settings, listen_port_setting, default_listen_port);

    if (port.empty() || port.size() > most_digits
        || string::npos != port.find_first_not_of("0123456789")) {
        throw invalid_argument(string(listen_port_setting)
                               + ": must be a positive whole number, not '" + port + "'");
    }

    const auto number = stoul(port);
    const auto max_port = numeric_limits<Address::Port>::max();

    if (number > max_port) {
        throw invalid_argument(string(listen_port_setting)
                               + ": cannot be greater than "
                               + to_string(max_port)
                               + ", not '" + port + "'");
    }

    return static_cast<Address::Port>(number);
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

    create_directories(cas);
    create_directories(incoming);
    store->add(cas);
    store->scan(incoming);
    return store;
}

// Each setting the file gives that the node uses, but for those logged
// whether it gives them or not; and each it does not use, as a mistyped name
// would be.
void log_given(const SettingsFile& settings) {
    for (const auto& name : settings.names()) {
        if (find(used_settings.begin(), used_settings.end(), name) == used_settings.end()) {
            lWarn << "Setting not used:" << name;
        } else if (find(always_logged.begin(), always_logged.end(), name)
                   == always_logged.end()) {
            lInfo << name << "=" << settings.value(name);
        }
    }
}

// SIGINT and SIGTERM, held back from the thread that makes this, and from
// each thread it then starts, until this is destroyed, for wait() to take.
class StopSignals {
public:
    StopSignals();
    ~StopSignals();

    // Waits for one of them, and says which.
    [[nodiscard]] string_view wait() const;

    StopSignals(const StopSignals&) = delete;
    StopSignals& operator=(const StopSignals&) = delete;
    StopSignals(StopSignals&&) = delete;
    StopSignals& operator=(StopSignals&&) = delete;

private:
    sigset_t m_signals{};
    sigset_t m_previous{};
};

StopSignals::StopSignals() {
    (void)sigemptyset(&m_signals);
    (void)sigaddset(&m_signals, SIGINT);
    (void)sigaddset(&m_signals, SIGTERM);

    if (const auto error = pthread_sigmask(SIG_BLOCK, &m_signals, &m_previous); 0 != error) {
        throw system_error(error, generic_category(), "pthread_sigmask");
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
        throw system_error(error, generic_category(), "sigwait");
    }

    return SIGINT == taken ? "SIGINT" : "SIGTERM";
}

} // namespace

namespace node {

void Node::run(const SettingsFile& settings, const vector<string>& seeds) {
    const StopSignals stop_signals;
    const Node node(settings, seeds);
    const auto signal = stop_signals.wait();

    lInfo << "Stopping on" << signal;
}

Node::Node(const SettingsFile& settings, const vector<string>& seeds)
    : m_log_level(applied_log_level(settings))
    , m_data_dir(data_dir(settings))
    , m_listen_address(listen_address(settings))
    , m_settings(_settings_of(settings, seeds))
    , m_store(lay_out(m_data_dir))
    , m_identity(m_data_dir / key_file_name, *m_store)
    , m_fetch(m_store, sessions_of(m_connections), m_settings.fetch)
    , m_service(m_listen_address, m_store, m_identity, looking_with(m_settings.service, m_fetch))
    , m_connections(m_identity, m_service.own_endpoints(), m_store,
                    MessageVerifier(make_shared<KnownKeys>(m_store), m_settings.service.window),
                    m_settings.connections)
    , m_seeker(m_fetch, sessions_of(m_connections), m_settings.seeker)
    , m_pusher(m_store, sessions_of(m_connections),
               m_settings.connections.fulfiller.pipeline_depth)
    , m_hand_off(m_store, sessions_of(m_connections))
    , m_evictor(m_identity.id(), m_store,
                Evictor::Peers{
                    [this](const Identifier& identifier) {
                        return m_hand_off.hand_off(identifier);
                    },
                    [this] { return m_connections.connected_ids(); },
                },
                Evictor::free_space_in(m_data_dir), m_settings.evictor)
    , m_recorder(m_data_dir / database_file_name, m_identity.id(), m_service.own_endpoints(),
                 m_settings.recorder) {
    lInfo << data_dir_setting << "=" << m_data_dir.string();
    lInfo << listen_address_setting << "=" << m_listen_address.host();
    lInfo << listen_port_setting << "=" << m_listen_address.port();
    lInfo << log_level_setting << "=" << text(settings, log_level_setting, default_log_level);
    log_given(settings);
    lInfo << "Seeds:" << m_settings.connections.seeds.size();
    lInfo << "Node id:" << m_identity.id().to_string();
    lInfo << "Listening on" << local_address().to_string();
}

const Address& Node::local_address() const noexcept {
    return m_service.local_address();
}

Node::PartSettings Node::_settings_of(const SettingsFile& settings, const vector<string>& seeds) {
    constexpr auto most_size = numeric_limits<size_t>::max();
    PartSettings parts;
    auto& service = parts.service;
    auto& fetch = parts.fetch;
    auto& connections = parts.connections;
    auto& sizes = connections.sizes;
    auto& fulfiller = connections.fulfiller;
    auto& search = parts.seeker.search;
    auto& evictor = parts.evictor;
    auto& recorder = parts.recorder;
    const auto retry_after =
        whole<size_t>(settings, retry_after_setting,
                      static_cast<size_t>(Service::Settings{}.retry_after.count()), 0,
                      static_cast<uintmax_t>(most_seconds));

    // The web server's.
    service.retry_after = std::chrono::seconds(retry_after);
    service.search_min_bits =
        whole(settings, search_min_bits_setting, service.search_min_bits, 0, most_search_bits);
    service.search_cache_ttl = seconds(settings, search_ttl_setting, service.search_cache_ttl);
    service.window.max_age = seconds(settings, max_age_setting, service.window.max_age);
    service.window.clock_skew =
        seconds(settings, clock_skew_setting, service.window.clock_skew, Zero::Allowed);
    service.provisional_trust_attempts =
        whole(settings, trust_attempts_setting, service.provisional_trust_attempts, 2);

    // The searches of the peers, for content and for prefixes.
    fetch.retry_after = service.retry_after;
    fetch.passes = whole(settings, search_passes_setting, fetch.passes, 2);
    fetch.failed_search_hold = seconds(settings, failed_hold_setting, fetch.failed_search_hold);
    search.passes = fetch.passes;
    search.pass_interval = fetch.retry_after;
    search.relays = whole(settings, search_relays_setting, search.relays, 0);
    search.hold = seconds(settings, broadcast_hold_setting, search.hold);
    parts.seeker.workers = whole(settings, search_workers_setting, parts.seeker.workers, 1);
    check_outlasts_searches(failed_hold_setting, fetch.failed_search_hold, fetch.passes,
                            fetch.retry_after);
    check_outlasts_searches(broadcast_hold_setting, search.hold, search.passes,
                            search.pass_interval);

    // The connections.
    sizes.min_primary_connections =
        whole(settings, min_outgoing_setting, sizes.min_primary_connections, 1);
    sizes.primary_prefix_bits =
        whole(settings, bucket_bits_setting, sizes.primary_prefix_bits, 1, most_prefix_bits);
    sizes.min_secondary_connections =
        whole(settings, min_neighborhood_setting, sizes.min_secondary_connections, 0);
    sizes.secondary_prefix_bits = whole(settings, neighborhood_bits_setting,
                                        sizes.secondary_prefix_bits, 1, most_prefix_bits);
    check_buckets(min_outgoing_setting, sizes.min_primary_connections, sizes.primary_prefix_bits);
    check_buckets(min_neighborhood_setting, sizes.min_secondary_connections,
                  sizes.secondary_prefix_bits);
    connections.limits.connect_timeout =
        seconds(settings, connect_timeout_setting, connections.limits.connect_timeout);
    connections.limits.request_timeout =
        seconds(settings, request_timeout_setting, connections.limits.request_timeout);
    connections.rests.retry_delay =
        seconds(settings, retry_delay_setting, connections.rests.retry_delay);
    connections.rests.turned_away_rest =
        seconds(settings, turned_away_setting, connections.rests.turned_away_rest);
    connections.seeds = seeds_of(settings, seeds);
    fulfiller.refresh = seconds(settings, seek_refresh_setting, fulfiller.refresh);
    fulfiller.search_min_bits = service.search_min_bits;
    fulfiller.pipeline_depth = whole(settings, pipeline_depth_setting, fulfiller.pipeline_depth, 1);

    if (fulfiller.refresh >= idle_timeout) {
        throw invalid_argument(string(seek_refresh_setting) + ": must be less than the "
                               + to_string(std::chrono::duration_cast<std::chrono::seconds>(
                                               idle_timeout)
                                               .count())
                               + " seconds a connection may be idle");
    }

    // The evictor's.
    if (settings.has(max_storage_setting)) {
        evictor.max_storage_bytes = whole_given(settings, max_storage_setting, 0,
                                                numeric_limits<uintmax_t>::max());
    }

    evictor.min_free_bytes = whole(settings, min_free_setting, evictor.min_free_bytes, 0);
    evictor.max_hand_offs = whole(settings, max_hand_offs_setting, evictor.max_hand_offs, 1);
    evictor.stored_grace =
        seconds(settings, stored_grace_setting, evictor.stored_grace, Zero::Allowed);
    evictor.candidates_timeout =
        seconds(settings, candidates_timeout_setting, evictor.candidates_timeout);

    // The recorder's.
    recorder.derive_interval = seconds(settings, derive_interval_setting, recorder.derive_interval);
    recorder.seek_entry_ttl = seconds(settings, seek_ttl_setting, recorder.seek_entry_ttl);
    recorder.max_candidates =
        whole(settings, max_candidates_setting, recorder.max_candidates, 1, most_size);
    recorder.limits.max_address_failures =
        whole(settings, address_failures_setting, recorder.limits.max_address_failures, 1);
    recorder.limits.max_node_failures =
        whole(settings, node_failures_setting, recorder.limits.max_node_failures, 1);

    return parts;
}

} // namespace node
