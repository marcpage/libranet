#pragma once

#include <chrono>
#include <cstdint>
#include <functional>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "db/Database.hpp"
#include "protocol/NodeList.hpp"
#include "storage/Identifier.hpp"

namespace stats {

// The addresses of nodes: a row for each node id and endpoint it may be
// reached at, saying how the address was learned and what came of dialing
// it. From the rows come the node list (HttpApi §10.6) and the candidates to
// dial. An address that has never worked is dropped once dialing it has
// failed max_address_failures times in a row; one that has worked is kept
// however often it fails. A node is no longer dialed once every address it
// has has failed max_node_failures times in a row.
//
// The rows are kept in the node_addresses table, which schema makes, in a
// database that must outlive this. Each statement is a transaction of its
// own, unless the caller has one open. Times are kept to the millisecond.
// Used from the one thread that uses the database.
// cppcheck-suppress noConstructor
class NodeAddresses {
public:
    using Clock = std::chrono::system_clock;
    using Now = std::function<Clock::time_point()>;

    // How an address was learned, weakest first. An address learned again
    // keeps the strongest way it has been.
    enum class Source : std::uint8_t {
        Relayed,    // Named in another node's list
        Advertised, // Named in the node's own list, as its own
        Observed,   // The address a connection from the node came from
        Dialed,     // Reached by this node, which found the node there
    };

    // What stats.max_address_failures and stats.max_node_failures are when
    // not set, as the Python node's are.
    static constexpr std::int64_t default_max_failures = 5;

    struct Limits {
        std::int64_t max_address_failures = default_max_failures;
        std::int64_t max_node_failures = default_max_failures;
    };

    // One row: an address of a node, and what came of dialing it.
    struct Address {
        std::string endpoint;
        Source source = Source::Relayed;
        Clock::time_point last_learned;
        std::optional<Clock::time_point> first_success; // None until it works
        std::optional<Clock::time_point> last_success;  // None until it works
        std::int64_t attempts = 0;
        std::int64_t consecutive_failures = 0; // Since it last worked
    };

    // A node to dial, with its endpoints in the order to try them.
    struct Candidate {
        storage::Identifier node_id;
        std::vector<std::string> endpoints;
    };

    // The table, made only if it is missing, for the database to make as it
    // opens. Times are milliseconds since the Unix epoch, and source is
    // written as its name, so that the table reads as it is.
    static constexpr std::string_view schema = "CREATE TABLE IF NOT EXISTS node_addresses ("
                                               "node_id TEXT NOT NULL, "
                                               "endpoint TEXT NOT NULL, "
                                               "source TEXT NOT NULL, "
                                               "last_learned INTEGER NOT NULL, "
                                               "first_success INTEGER, "
                                               "last_success INTEGER, "
                                               "attempts INTEGER NOT NULL DEFAULT 0, "
                                               "consecutive_failures INTEGER NOT NULL DEFAULT 0, "
                                               "PRIMARY KEY (node_id, endpoint))";

    // limits are stats.max_address_failures and stats.max_node_failures.
    // Throws std::invalid_argument for a limit less than 1, or now with no
    // function.
    NodeAddresses(db::Database& database, Limits limits, Now now = &Clock::now);

    // Records each entry of list as an address of its node, learned from
    // source now. One already known keeps the stronger of its source and
    // source. Each throws db::SqliteError when SQLite cannot record it, as
    // when the database has no such table.
    void record_learned(const protocol::NodeList& list, Source source);
    // Records that dialing endpoint reached node_id, which proved its
    // identity there now: the address is added if it is new, and is Dialed,
    // and has worked, with no failures since. Also throws
    // std::invalid_argument for an endpoint that is not one, as NodeList
    // does.
    void record_success(const storage::Identifier& node_id, std::string_view endpoint);
    // Records that dialing endpoint did not reach node_id, if the address is
    // known, and drops it if it has never worked and has now failed
    // max_address_failures times in a row.
    void record_failure(const storage::Identifier& node_id, std::string_view endpoint);

    // Each address of node_id, in the order they are tried: those that have
    // worked, the most recently first, then the rest, the most recently
    // learned first. Each throws db::SqliteError when SQLite cannot read
    // them, and std::invalid_argument for a row the table should not hold,
    // as one of an unknown source.
    [[nodiscard]] std::vector<Address> addresses(const storage::Identifier& node_id) const;
    // A node list naming own_endpoints, own_id's, first, then each other
    // node's last address that worked, unless it has failed since, the most
    // recently reached first.
    [[nodiscard]] protocol::NodeList node_list(const storage::Identifier& own_id,
                                               const std::vector<std::string>& own_endpoints) const;
    // Each node but own_id still to be dialed: those that have been reached,
    // the most recently first, then the rest, the most recently learned
    // first.
    [[nodiscard]] std::vector<Candidate> candidates(const storage::Identifier& own_id) const;

private:
    // Mistaken: the constructor sets m_database. cppcheck loses the class's
    // constructors in a file that names it with `using stats::NodeAddresses;`.
    // cppcheck-suppress uninitMemberVarNoCtor
    db::Database* m_database;
    Limits m_limits;
    Now m_now;
};

} // namespace stats
