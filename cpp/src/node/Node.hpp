#pragma once

#include <filesystem>
#include <thread>

#include "api/DataHandler.hpp"
#include "api/NodesHandler.hpp"
#include "api/SignatureGuard.hpp"
#include "identity/NodeIdentity.hpp"
#include "logger/Logger.hpp"
#include "net/Address.hpp"
#include "node/SettingsFile.hpp"
#include "storage/Manager.hpp"
#include "web/Server.hpp"

namespace node {

// A Libranet node. It lays out its data directory, makes what its parts
// share, and then makes its parts, which run threads of their own. When it
// goes, it lets them go, last made first, each stopping its threads. For
// now it has one part: the web server, which answers /data from the store.
//
// It uses these settings, with the Python node's names and defaults:
//
// - storage.data_dir: where it keeps what it holds. By default, the user's
//   data directory for libranet, as the Python node's: on macOS,
//   ~/Library/Application Support/libranet, and elsewhere,
//   $XDG_DATA_HOME/libranet or ~/.local/share/libranet.
// - network.listen_address: the address it listens on; by default "::",
//   every address, IPv6 and IPv4.
// - network.listen_port: the port it listens on, 8080 by default, or 0 for
//   one the system picks.
// - logging.level: the least severe lines it logs, one of CRITICAL, ERROR,
//   WARNING, INFO (the default), and DEBUG. Lines go to stderr.
//
// The data directory holds cas/, the content the node holds; incoming/,
// whose directories are read as content too; and node-key.pem, the node's
// key, made the first time it starts, which only its owner may read.
class Node {
public:
    // Runs a node with the settings until the process is sent SIGINT or
    // SIGTERM, then lets it go. While it runs, those signals are held back
    // from every thread it starts, to be taken here.
    static void run(const SettingsFile& settings);

    // Throws std::invalid_argument, naming the setting, for a setting it
    // cannot use, before it makes anything; and std::system_error or
    // std::runtime_error when it cannot lay out the data directory, keep its
    // key, or listen.
    explicit Node(const SettingsFile& settings);
    ~Node();
    Node(const Node&) = delete;
    Node& operator=(const Node&) = delete;
    Node(Node&&) = delete;
    Node& operator=(Node&&) = delete;

    // The address the node listens on, with the port the system picked when
    // the settings left that to it.
    [[nodiscard]] const net::Address& local_address() const noexcept;

private:
    // From the settings, each checked before the node makes anything.
    logger::Logger::Level m_log_level;
    std::filesystem::path m_data_dir;
    net::Address m_listen_address;

    // What the parts share.
    storage::Manager::Ptr m_store;
    identity::NodeIdentity m_identity;

    // The parts.
    api::DataHandler m_data_handler;
    api::SignatureGuard m_guard; // In front of each of m_server's handlers
    web::Server m_server;
    net::Address m_local_address; // Where m_server listens
    // Naming m_local_address's port as the node's own, so made after it.
    api::NodesHandler m_nodes_handler;
    std::thread m_server_thread;

    void _serve();
};

} // namespace node
