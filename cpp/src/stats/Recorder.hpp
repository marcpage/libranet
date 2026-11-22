#pragma once

#include <chrono>
#include <filesystem>
#include <map>
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

// What the node remembers of the network, kept in its database, which only
// this opens. From a thread of its own, it records the addresses each node
// list received names and what came of each dial; how content is used
// (ContentUses), from each storage::DataStored and storage::DataRequested;
// and what the node seeks (Sought): the content each data.not_found names,
// until a data.stored names it, and the prefix of each search. Every derive
// interval it derives the node list (HttpApi §10.6), the candidates to dial
// (NodeAddresses), and the seek list (§10.7.1), posting each when it has
// changed. The seek list is posted at once, too, when an event changes what
// it names, so that the peers reading it learn of the change as soon as
// they can. The first derivation is made as the thread starts, and posts
// all three, so that what takes them knows them from the start.
//
// The events it takes and posts are below, each under its name, as the
// Python node names it where it has the same event. An event is a plain
// struct, read by whatever takes it. Its conversion to text, which
// msg::Message says what it holds with, is a member, as a conversion must
// be, so the check takes it for a class.
class Recorder {
public:
    using Clock = NodeAddresses::Clock;
    using Milliseconds = std::chrono::milliseconds;
    using Now = NodeAddresses::Now;
    using Source = NodeAddresses::Source;

    // NOLINTBEGIN(*-non-private-member-variables-in-classes)

    // Addresses learned: a node list received, and how each endpoint it
    // names was learned. One sources does not name was relayed.
    struct NodesReceived {
        static constexpr auto name = "nodes.received";

        protocol::NodeList nodes;
        std::map<std::string, Source> sources;

        explicit operator std::string() const;
    };

    // A dial of endpoint that reached node_id, which proved its identity
    // there.
    struct ConnectionOpened {
        static constexpr auto name = "connection.opened";

        storage::Identifier node_id;
        std::string endpoint;

        explicit operator std::string() const;
    };

    // A dial of endpoint that did not reach node_id: nothing answered, or
    // some other node did.
    struct ConnectionFailed {
        static constexpr auto name = "connection.failed";

        storage::Identifier node_id;
        std::string endpoint;

        explicit operator std::string() const;
    };

    // The node list, which has changed, and the entries it names that the
    // list posted before did not. Before the first, the node list named the
    // node's own endpoints alone, so added never names them.
    struct NodesUpdated {
        static constexpr auto name = "nodes.updated";

        protocol::NodeList nodes;
        protocol::NodeList added;

        explicit operator std::string() const;
    };

    // The candidates to dial, which have changed.
    struct CandidatesUpdated {
        static constexpr auto name = "candidates.updated";

        std::vector<NodeAddresses::Candidate> candidates;

        explicit operator std::string() const;
    };

    // A search for what has a hash that begins with prefix, which the node
    // seeks from its peers too.
    struct SearchRequested {
        static constexpr auto name = "data.search_requested";

        storage::HashPrefix prefix;

        explicit operator std::string() const;
    };

    // The seek list, which has changed.
    struct SeekUpdated {
        static constexpr auto name = "seek.updated";

        protocol::SeekList seek;

        explicit operator std::string() const;
    };

    // NOLINTEND(*-non-private-member-variables-in-classes)

    // What stats.derive_interval_seconds is when not set, as the Python
    // node's is.
    static constexpr Milliseconds default_derive_interval{60'000};

    struct Settings {
        Milliseconds derive_interval = default_derive_interval; // stats.derive_interval_seconds
        NodeAddresses::Limits limits;
        Milliseconds seek_entry_ttl = Sought::default_entry_ttl; // stats.seek_entry_ttl_seconds
    };

    // Opens the database at database_path, making it and its tables if they
    // are missing, and starts the thread. own_id and own_endpoints are this
    // node's, which the node list names first. Throws std::invalid_argument
    // for an own endpoint that is not one, a derive interval or seek entry
    // ttl that is not more than zero, a limit less than 1, or now with no
    // function; and db::SqliteError when the database cannot be opened.
    Recorder(const std::filesystem::path& database_path, storage::Identifier own_id,
             std::vector<std::string> own_endpoints, Settings settings,
             Now now = &Clock::now);
    // Stops the thread. What it has not yet recorded is let go.
    ~Recorder() = default;
    // Its thread holds this object, so it is neither copied nor moved.
    Recorder(const Recorder&) = delete;
    Recorder& operator=(const Recorder&) = delete;
    Recorder(Recorder&&) = delete;
    Recorder& operator=(Recorder&&) = delete;

private:
    // The name the timer posts under, for the thread to derive the lists.
    static constexpr auto derive_due = "stats.derive_due";

    storage::Identifier m_own_id;
    std::vector<std::string> m_own_endpoints;
    Milliseconds m_derive_interval;
    db::Database m_database;
    // Mistaken: the constructor sets m_addresses. cppcheck loses
    // NodeAddresses's constructors, as NodeAddresses.hpp says.
    // cppcheck-suppress uninitMemberVarPrivate
    NodeAddresses m_addresses;
    // Mistaken, as for m_addresses: cppcheck loses ContentUses's
    // constructors too.
    // cppcheck-suppress uninitMemberVarPrivate
    ContentUses m_content_uses;
    // Mistaken: the constructor sets m_sought. cppcheck loses Sought's
    // constructors, as Sought.hpp says.
    // cppcheck-suppress uninitMemberVarPrivate
    Sought m_sought;

    // As last posted, used only by the thread. Before the first derivation,
    // the node list names the node's own endpoints alone.
    bool m_derived = false;
    protocol::NodeList m_node_list;
    std::vector<NodeAddresses::Candidate> m_candidates;
    protocol::SeekList m_seek_list;

    // Made last, and so stopped first: the timer, then the thread, which
    // uses all of the above.
    msg::AsyncChannel m_channel;
    msg::Timer m_timer;

    void _handle(const msg::IMessage::Ptr& message);
    void _derive();
    void _post_seek_list();
};

} // namespace stats
