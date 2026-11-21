#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <optional>
#include <string_view>

#include "db/Database.hpp"
#include "storage/Identifier.hpp"

namespace stats {

// How content is used: a row for each object stored or asked for, held or
// not, with its size as stored and when it was last stored, and when it was
// last asked for and how often. Content stored again, once deleted, takes
// the size and time it was stored again; the requests are kept.
//
// The rows are kept in the content_uses table, which schema makes, in a
// database that must outlive this. Each statement is a transaction of its
// own, unless the caller has one open. Times are kept to the millisecond.
// Used from the one thread that uses the database.
class ContentUses {
public:
    using Clock = std::chrono::system_clock;
    using Now = std::function<Clock::time_point()>;

    // What is known of one object.
    struct Uses {
        std::optional<std::size_t> size;                 // As stored; none until stored
        std::optional<Clock::time_point> last_stored;    // None until stored
        std::optional<Clock::time_point> last_requested; // None until asked for
        std::int64_t requests = 0;
    };

    // The table, made only if it is missing, for the database to make as it
    // opens. Times are milliseconds since the Unix epoch.
    static constexpr std::string_view schema = "CREATE TABLE IF NOT EXISTS content_uses ("
                                               "identifier TEXT NOT NULL PRIMARY KEY, "
                                               "size INTEGER, "
                                               "last_stored INTEGER, "
                                               "last_requested INTEGER, "
                                               "requests INTEGER NOT NULL DEFAULT 0)";

    // Throws std::invalid_argument for now with no function.
    explicit ContentUses(db::Database& database, Now now = &Clock::now);

    // Records that identifier was stored now, size bytes as stored. Each
    // throws db::SqliteError when SQLite cannot record it, as when the
    // database has no such table.
    void record_stored(const storage::Identifier& identifier, std::size_t size);
    // Records that identifier was asked for now, held or not.
    void record_requested(const storage::Identifier& identifier);

    // What is known of identifier, or nothing if it was never stored nor
    // asked for. Throws db::SqliteError when SQLite cannot read it.
    [[nodiscard]] std::optional<Uses> uses(const storage::Identifier& identifier) const;

private:
    db::Database* m_database;
    Now m_now;
};

} // namespace stats
