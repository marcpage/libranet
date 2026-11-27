#pragma once

#include <chrono>
#include <cstddef>
#include <filesystem>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "ContentUses.hpp"
#include "NodeAddresses.hpp"
#include "Sought.hpp"
#include "db/Database.hpp"
#include "msg/AsyncChannel.hpp"
#include "msg/IMessage.hpp"
#include "msg/Timer.hpp"
#include "protocol/NodeList.hpp"
#include "protocol/SeekList.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"

namespace stats {

// What the node remembers of the network and of how content is used, in a
// database only this opens.
class Recorder {
public:
    using Clock = NodeAddresses::Clock;
    using Milliseconds = std::chrono::milliseconds;
    using ClockFunction = NodeAddresses::ClockFunction;
    using Source = NodeAddresses::Source;

    // Events: plain structs, but for their conversion to text.
    // NOLINTBEGIN(*-non-private-member-variables-in-classes)

    // How each endpoint was learned; one sources does not name was relayed.
    struct NodesReceived {
        static constexpr auto name = "nodes.received";

        protocol::NodeList nodes;
        std::map<std::string, Source> sources;

        explicit operator std::string() const;
    };

    struct ConnectionOpened {
        static constexpr auto name = "connection.opened";

        storage::Identifier node_id;
        std::string endpoint;

        explicit operator std::string() const;
    };

    // Nothing answered, or some other node did.
    struct ConnectionFailed {
        static constexpr auto name = "connection.failed";

        storage::Identifier node_id;
        std::string endpoint;

        explicit operator std::string() const;
    };

    // added names the entries the list posted before did not.
    struct NodesUpdated {
        static constexpr auto name = "nodes.updated";

        protocol::NodeList nodes;
        protocol::NodeList added;

        explicit operator std::string() const;
    };

    struct CandidatesUpdated {
        static constexpr auto name = "candidates.updated";

        std::vector<NodeAddresses::Candidate> candidates;

        explicit operator std::string() const;
    };

    // node is the node that asked, if one did, and relays what it said in
    // X-Search-Relays, if usable (HttpApi §6).
    struct SearchRequested {
        static constexpr auto name = "data.search_requested";

        storage::HashPrefix prefix;
        std::optional<storage::Identifier> node = std::nullopt;
        std::size_t relays = 0;

        explicit operator std::string() const;
    };

    struct SeekUpdated {
        static constexpr auto name = "seek.updated";

        protocol::SeekList seek;

        explicit operator std::string() const;
    };

    // NOLINTEND(*-non-private-member-variables-in-classes)

    // As the Python node's are.
    static constexpr Milliseconds default_derive_interval{60'000};
    static constexpr std::size_t default_max_candidates = 256;

    struct Settings {
        Milliseconds derive_interval = default_derive_interval; // stats.derive_interval_seconds
        NodeAddresses::Limits limits;
        Milliseconds seek_entry_ttl = Sought::default_entry_ttl; // stats.seek_entry_ttl_seconds
        std::size_t max_candidates = default_max_candidates;     // storage.max_candidates
    };

    // own_id and own_endpoints are this node's. Throws std::invalid_argument
    // for a setting it cannot use, an own endpoint that is not one, or no
    // clock; and db::SqliteError when the database cannot be opened.
    Recorder(const std::filesystem::path& database_path, storage::Identifier own_id,
             std::vector<std::string> own_endpoints, Settings settings,
             ClockFunction clock = &Clock::now);

    // What it has not yet recorded is let go.
    ~Recorder() = default;

    // Its thread holds this object, so it is neither copied nor moved.
    Recorder(const Recorder&) = delete;
    Recorder& operator=(const Recorder&) = delete;
    Recorder(Recorder&&) = delete;
    Recorder& operator=(Recorder&&) = delete;

private:
    static constexpr auto derive_due = "stats.derive_due";

    storage::Identifier m_own_id;
    std::vector<std::string> m_own_endpoints;
    Milliseconds m_derive_interval;
    // Mistaken: cppcheck loses the constructor in files `using` the class.
    // cppcheck-suppress uninitMemberVarPrivate
    std::size_t m_max_candidates;
    ClockFunction m_clock;
    db::Database m_database;
    // Mistaken: cppcheck loses NodeAddresses's constructors.
    // cppcheck-suppress uninitMemberVarPrivate
    NodeAddresses m_addresses;
    // Mistaken: cppcheck loses ContentUses's constructors.
    // cppcheck-suppress uninitMemberVarPrivate
    ContentUses m_content_uses;
    // Mistaken: cppcheck loses Sought's constructors.
    // cppcheck-suppress uninitMemberVarPrivate
    Sought m_sought;

    // Used only by the thread.
    bool m_derived = false;
    protocol::NodeList m_node_list;
    std::vector<NodeAddresses::Candidate> m_candidates;
    protocol::SeekList m_seek_list;

    // Made last, and so stopped first.
    msg::AsyncChannel m_channel;
    msg::Timer m_timer;

    void _handle(const msg::IMessage::Ptr& message);
    void _derive();
    void _post_seek_list();
};

} // namespace stats
