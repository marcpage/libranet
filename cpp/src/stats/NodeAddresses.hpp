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

// The addresses of nodes, what came of dialing them, and the node list and
// candidates to dial that come from them (HttpApi §10.6), in a table of a
// database that must outlive this. Used from the one thread that uses it.
// cppcheck-suppress noConstructor
class NodeAddresses {
public:
    using Clock = std::chrono::system_clock;
    using ClockFunction = std::function<Clock::time_point()>;

    // Weakest first. An address learned again keeps the strongest.
    enum class Source : std::uint8_t {
        Relayed,    // Named in another node's list
        Advertised, // Named in the node's own list, as its own
        Observed,   // The address a connection from the node came from
        Dialed,     // Reached by this node, which found the node there
    };

    // As the Python node's are.
    static constexpr std::int64_t default_max_failures = 5;

    struct Limits {
        std::int64_t max_address_failures = default_max_failures;
        std::int64_t max_node_failures = default_max_failures;
    };

    struct Address {
        std::string endpoint;
        Source source = Source::Relayed;
        Clock::time_point last_learned;
        std::optional<Clock::time_point> first_success; // None until it works
        std::optional<Clock::time_point> last_success;  // None until it works
        std::int64_t attempts = 0;
        std::int64_t consecutive_failures = 0; // Since it last worked
    };

    // Endpoints in the order to try them.
    struct Candidate {
        storage::Identifier node_id;
        std::vector<std::string> endpoints;
    };

    // Times are milliseconds since the Unix epoch; source is its name.
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

    // Throws std::invalid_argument for a limit less than 1, or no clock.
    NodeAddresses(db::Database& database, Limits limits, ClockFunction clock = &Clock::now);

    // An address already known keeps the stronger source. Each throws
    // db::SqliteError when SQLite cannot record it.
    void record_learned(const protocol::NodeList& list, Source source);
    // Also throws std::invalid_argument for an endpoint that is not one.
    void record_success(const storage::Identifier& node_id, std::string_view endpoint);
    // Drops an address that has never worked once it fails
    // max_address_failures times in a row.
    void record_failure(const storage::Identifier& node_id, std::string_view endpoint);

    // In the order they are tried. Each throws db::SqliteError, or
    // std::invalid_argument for a row the table should not hold.
    [[nodiscard]] std::vector<Address> addresses(const storage::Identifier& node_id) const;
    // own_endpoints first, then each other node's last address that worked.
    [[nodiscard]] protocol::NodeList node_list(const storage::Identifier& own_id,
                                               const std::vector<std::string>& own_endpoints) const;
    // Each node but own_id still to be dialed, those reached most recently
    // first.
    [[nodiscard]] std::vector<Candidate> candidates(const storage::Identifier& own_id) const;

private:
    // Mistaken: cppcheck loses the constructors in files `using` the class.
    // cppcheck-suppress uninitMemberVarNoCtor
    db::Database* m_database;
    Limits m_limits;
    ClockFunction m_clock;
};

} // namespace stats
