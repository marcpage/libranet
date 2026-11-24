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

// A Libranet node. It lays out its data directory, makes what its parts
// share, and then makes its parts, which run threads of their own: the web
// server, which answers /data (api::Service); the outgoing connections,
// which keep up the peer mix (peer::Connections); the seeker, which fetches
// from the peers what the node lacks and searches them (peer::Seeker); the
// pusher, which passes new content on (peer::Pusher); the evictor, which
// keeps storage within its limits, handing content off to the peers
// (eviction::Evictor, peer::HandOff); and the recorder, which remembers the
// network and how content is used (stats::Recorder). When it goes, it lets
// them go, last made first, each stopping its threads.
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
// - peers.seed_file: a seed list, laid out as data/seed_peers.json is, of
//   the nodes to dial while the node knows no other, after those the
//   command line names. By default, none.
// - The settings its parts take, as each part says: network.
//   retry_after_seconds; peers.min_outgoing_connections,
//   bucket_prefix_bits, min_neighborhood_connections,
//   neighborhood_prefix_bits, connect_timeout_seconds,
//   request_timeout_seconds, retry_delay_seconds, turned_away_rest_seconds,
//   seek_refresh_seconds, search_passes, search_relays,
//   failed_search_hold_seconds, broadcast_hold_seconds, search_workers, and
//   pipeline_depth; storage.min_free_bytes, max_storage_bytes,
//   max_hand_offs, candidates_timeout_seconds, max_candidates,
//   stored_grace_seconds, search_cache_ttl_seconds, and search_min_bits;
//   identity.provisional_trust_attempts, signature_max_age_seconds, and
//   signature_clock_skew_seconds; and stats.derive_interval_seconds,
//   seek_entry_ttl_seconds, max_address_failures, and max_node_failures.
//   Each is refused where the Python node refuses it.
//
// The data directory holds cas/, the content the node holds; incoming/,
// whose directories are read as content too; node-key.pem, the node's key,
// made the first time it starts, which only its owner may read; and
// libranet.sqlite3, what the recorder remembers.
class Node {
public:
    // Runs a node with the settings and the seeds until the process is sent
    // SIGINT or SIGTERM, then lets it go. While it runs, those signals are
    // held back from every thread it starts, to be taken here.
    static void run(const SettingsFile& settings, const std::vector<std::string>& seeds = {});

    // Each of seeds names a node to dial while the node knows no other, as
    // --seed does: URL[=ID], an http endpoint, and the node id there, if it
    // is known. Throws std::invalid_argument, naming the setting or --seed,
    // for one it cannot use, before it makes anything; and std::system_error
    // or std::runtime_error when it cannot lay out the data directory, keep
    // its key, or listen, and db::SqliteError when it cannot open its
    // database.
    explicit Node(const SettingsFile& settings, const std::vector<std::string>& seeds = {});

    // The address the node listens on, with the port the system picked when
    // the settings left that to it.
    [[nodiscard]] const net::Address& local_address() const noexcept;

    ~Node() = default;

    Node(const Node&) = delete;
    Node& operator=(const Node&) = delete;
    Node(Node&&) = delete;
    Node& operator=(Node&&) = delete;

private:
    // What the settings and the seeds give each part.
    struct PartSettings {
        api::Service::Settings service;
        peer::Fetch::Settings fetch;
        peer::Connections::Settings connections;
        peer::Seeker::Settings seeker;
        eviction::Evictor::Settings evictor;
        stats::Recorder::Settings recorder;
    };

    // From the settings, each checked before the node makes anything.
    logger::Logger::Level m_log_level;
    std::filesystem::path m_data_dir;
    net::Address m_listen_address;
    PartSettings m_settings;

    // What the parts share. The fetch is the searches for content the node
    // lacks, which the web server asks whether it looks for, and the seeker
    // makes. They ask the connections for the peers, though those are made
    // after it, as only the seeker, made after them and gone before them,
    // makes one.
    storage::Manager::Ptr m_store;
    identity::NodeIdentity m_identity;
    peer::Fetch m_fetch;

    // The parts, each made after those it uses.
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
