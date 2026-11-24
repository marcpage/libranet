#pragma once

#include <chrono>
#include <functional>
#include <string_view>

#include "db/Database.hpp"
#include "protocol/SeekList.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"

namespace stats {

// What the node seeks and does not hold (HttpApi §10.7), in a table of a
// database that must outlive this. Used from the one thread that uses it.
// cppcheck-suppress noConstructor
class Sought {
public:
    using Clock = std::chrono::system_clock;
    using Milliseconds = std::chrono::milliseconds;
    using ClockFunction = std::function<Clock::time_point()>;

    // An hour, as the Python node's is.
    static constexpr Milliseconds default_entry_ttl{3'600'000};

    // kind is "data" or "search"; sought_at is in milliseconds since the Unix
    // epoch.
    static constexpr std::string_view schema = "CREATE TABLE IF NOT EXISTS sought ("
                                               "kind TEXT NOT NULL, "
                                               "value TEXT NOT NULL, "
                                               "sought_at INTEGER NOT NULL, "
                                               "PRIMARY KEY (kind, value))";

    // Throws std::invalid_argument for an entry_ttl not more than zero, or no
    // clock.
    Sought(db::Database& database, Milliseconds entry_ttl, ClockFunction clock = &Clock::now);

    // Each says whether the seek list did not name it already. Each throws
    // db::SqliteError when SQLite cannot record it.
    bool seek(const storage::Identifier& identifier);
    bool seek(const storage::HashPrefix& prefix);
    // Says whether the seek list named it.
    bool found(const storage::Identifier& identifier);
    void forget_expired();

    // The most recently sought first of each kind. Throws db::SqliteError, or
    // std::invalid_argument for an entry the table should not hold.
    [[nodiscard]] protocol::SeekList seek_list() const;

private:
    // Mistaken: cppcheck loses the constructors in files `using` the class.
    // cppcheck-suppress uninitMemberVarNoCtor
    db::Database* m_database;
    Milliseconds m_entry_ttl;
    ClockFunction m_clock;
};

} // namespace stats
