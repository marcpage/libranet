#pragma once

#include <chrono>
#include <functional>
#include <string_view>

#include "db/Database.hpp"
#include "protocol/SeekList.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"

namespace stats {

// What the node seeks and does not hold (HttpApi §10.7): the identifiers of
// content it is fetching, and of content peers named that it lacks and
// wants (§10.7.2), and the hash prefixes it is searching for. Each is sought
// until it is found, or entry_ttl has passed since it was last sought. The
// seek list names what is sought now, the most recently sought first.
//
// The entries are kept in the sought table, which schema makes, in a
// database that must outlive this. Each statement is a transaction of its
// own, unless the caller has one open. Times are kept to the millisecond.
// Used from the one thread that uses the database.
// cppcheck-suppress noConstructor
class Sought {
public:
    using Clock = std::chrono::system_clock;
    using Milliseconds = std::chrono::milliseconds;
    using Now = std::function<Clock::time_point()>;

    // What stats.seek_entry_ttl_seconds is when not set, as the Python
    // node's is: an hour.
    static constexpr Milliseconds default_entry_ttl{3'600'000};

    // The table, made only if it is missing, for the database to make as it
    // opens. kind is the seek list's key for the entry, "data" or "search",
    // and value its text there. sought_at is when it was last sought, in
    // milliseconds since the Unix epoch.
    static constexpr std::string_view schema = "CREATE TABLE IF NOT EXISTS sought ("
                                               "kind TEXT NOT NULL, "
                                               "value TEXT NOT NULL, "
                                               "sought_at INTEGER NOT NULL, "
                                               "PRIMARY KEY (kind, value))";

    // entry_ttl is stats.seek_entry_ttl_seconds. Throws
    // std::invalid_argument for an entry_ttl that is not more than zero, or
    // now with no function.
    Sought(db::Database& database, Milliseconds entry_ttl, Now now = &Clock::now);

    // Each seeks what it is given from now, as a data or a search entry,
    // and says whether the seek list did not name it already. Each throws
    // db::SqliteError when SQLite cannot record it, as when the database
    // has no such table.
    bool seek(const storage::Identifier& identifier);
    bool seek(const storage::HashPrefix& prefix);
    // Seeks identifier no more, now that the node holds it, and says whether
    // the seek list named it.
    bool found(const storage::Identifier& identifier);
    // Forgets each entry whose entry_ttl has passed, which the seek list no
    // longer names.
    void forget_expired();

    // What is sought now, the most recently sought first of each kind.
    // Throws db::SqliteError when SQLite cannot read it, and
    // std::invalid_argument for an entry the table should not hold, as one
    // that is not an identifier.
    [[nodiscard]] protocol::SeekList seek_list() const;

private:
    // Mistaken: the constructor sets m_database. cppcheck loses the class's
    // constructors in a file that names it with `using stats::Sought;`.
    // cppcheck-suppress uninitMemberVarNoCtor
    db::Database* m_database;
    Milliseconds m_entry_ttl;
    Now m_now;
};

} // namespace stats
