#pragma once

#include <filesystem>
#include <string>
#include <vector>

#include "api/Service.hpp"
#include "eviction/Evictor.hpp"
#include "identity/NodeIdentity.hpp"
#include "logger/Logger.hpp"
#include "net/Address.hpp"
#include "node/SettingsFile.hpp"
#include "peer/Connections.hpp"
#include "peer/Fetch.hpp"
#include "peer/HandOff.hpp"
#include "peer/Pusher.hpp"
#include "peer/Seeker.hpp"
#include "stats/Recorder.hpp"
#include "storage/Manager.hpp"

namespace node {

// A Libranet node: it lays out its data directory and makes its parts, each
// with threads of its own, with the Python node's settings and defaults.
class Node {
public:
    // Runs a node until the process is sent SIGINT or SIGTERM, which are held
    // back from every thread it starts.
    static void run(const SettingsFile& settings, const std::vector<std::string>& seeds = {});

    // Each of seeds is as --seed gives it: URL[=ID]. Throws
    // std::invalid_argument, naming the setting or --seed, for one it cannot
    // use, before it makes anything; std::system_error or std::runtime_error
    // when it cannot lay out its data directory, keep its key, or listen; and
    // db::SqliteError when it cannot open its database.
    explicit Node(const SettingsFile& settings, const std::vector<std::string>& seeds = {});

    // With the port the system picked when the settings left that to it.
    [[nodiscard]] const net::Address& local_address() const noexcept;

    ~Node() = default;

    Node(const Node&) = delete;
    Node& operator=(const Node&) = delete;
    Node(Node&&) = delete;
    Node& operator=(Node&&) = delete;

private:
    struct PartSettings {
        api::Service::Settings service;
        peer::Fetch::Settings fetch;
        peer::Connections::Settings connections;
        peer::Seeker::Settings seeker;
        eviction::Evictor::Settings evictor;
        stats::Recorder::Settings recorder;
    };

    // Each checked before the node makes anything.
    logger::Logger::Level m_log_level;
    std::filesystem::path m_data_dir;
    net::Address m_listen_address;
    PartSettings m_settings;

    // What the parts share. The fetch asks the connections for the peers,
    // though they are made after it, as only the seeker, made after them and
    // gone before them, uses it.
    storage::Manager::Ptr m_store;
    identity::NodeIdentity m_identity;
    peer::Fetch m_fetch;

    // Each made after those it uses.
    api::Service m_service;
    peer::Connections m_connections;
    peer::Seeker m_seeker;
    peer::Pusher m_pusher;
    peer::HandOff m_hand_off;
    eviction::Evictor m_evictor;
    // Made last, as the lists it posts as it starts are for the parts above.
    stats::Recorder m_recorder;

    [[nodiscard]] static PartSettings _settings_of(const SettingsFile& settings,
                                                   const std::vector<std::string>& seeds);
};

} // namespace node
