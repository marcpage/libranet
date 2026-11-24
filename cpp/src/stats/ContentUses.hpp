#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <optional>
#include <string_view>
#include <vector>

#include "db/Database.hpp"
#include "storage/Identifier.hpp"

namespace stats {

// How content is used: a row for each object stored or asked for, held or not,
// in a table of a database that must outlive this. Used from the one thread
// that uses it.
class ContentUses {
public:
    using Clock = std::chrono::system_clock;
    using ClockFunction = std::function<Clock::time_point()>;

    struct Uses {
        std::optional<std::size_t> size;                 // As stored; none unless held
        std::optional<Clock::time_point> last_stored;    // None until stored
        std::optional<Clock::time_point> last_requested; // None until asked for
        std::int64_t requests = 0;
    };

    struct Held {
        storage::Identifier identifier;
        Uses uses;
    };

    // Times are milliseconds since the Unix epoch.
    static constexpr std::string_view schema = "CREATE TABLE IF NOT EXISTS content_uses ("
                                               "identifier TEXT NOT NULL PRIMARY KEY, "
                                               "size INTEGER, "
                                               "last_stored INTEGER, "
                                               "last_requested INTEGER, "
                                               "requests INTEGER NOT NULL DEFAULT 0)";

    // Throws std::invalid_argument for no clock.
    explicit ContentUses(db::Database& database, ClockFunction clock = &Clock::now);

    // Each throws db::SqliteError when SQLite cannot record it.
    void record_stored(const storage::Identifier& identifier, std::size_t size);
    void record_requested(const storage::Identifier& identifier);
    // What else is known of it is kept.
    void record_deleted(const storage::Identifier& identifier);

    // Each throws db::SqliteError when SQLite cannot read it.
    [[nodiscard]] std::optional<Uses> uses(const storage::Identifier& identifier) const;
    // In no particular order.
    [[nodiscard]] std::vector<Held> held() const;

private:
    db::Database* m_database;
    ClockFunction m_clock;
};

} // namespace stats
