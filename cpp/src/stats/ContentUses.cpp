#include "ContentUses.hpp"

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string_view>
#include <utility>

#include "db/Database.hpp"
#include "db/Statement.hpp"
#include "storage/Identifier.hpp"

using db::Database;
using db::Statement;
using stats::ContentUses;
using std::int64_t;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::size_t;
using std::string_view;
using std::chrono::duration_cast;
using std::chrono::milliseconds;
using storage::Identifier;

namespace {

using Clock = ContentUses::Clock;
using Uses = ContentUses::Uses;

// The columns uses_sql reads, in the order it reads them.
enum class Column : std::uint8_t {
    Size,
    LastStored,
    LastRequested,
    Requests,
};

// Records content stored: ?1 is its identifier, ?2 its size, and ?3 when.
constexpr string_view stored_sql =
    "INSERT INTO content_uses (identifier, size, last_stored) VALUES (?1, ?2, ?3) "
    "ON CONFLICT (identifier) DO UPDATE SET size = excluded.size, "
    "last_stored = excluded.last_stored";

// Records content asked for: ?1 is its identifier, and ?2 when.
constexpr string_view requested_sql =
    "INSERT INTO content_uses (identifier, last_requested, requests) VALUES (?1, ?2, 1) "
    "ON CONFLICT (identifier) DO UPDATE SET last_requested = excluded.last_requested, "
    "requests = requests + 1";

// What is known of the content ?1.
constexpr string_view uses_sql = "SELECT size, last_stored, last_requested, requests "
                                 "FROM content_uses WHERE identifier = ?1";

// The milliseconds since the Unix epoch of time, as the table holds a time.
int64_t milliseconds_of(Clock::time_point time) {
    return duration_cast<milliseconds>(time.time_since_epoch()).count();
}

// Where column is in the row uses_sql reads, as Statement numbers columns.
int place_of(Column column) {
    return static_cast<int>(column);
}

// The time column holds in the row statement is on, if any.
optional<Clock::time_point> optional_time(const Statement& statement, Column column) {
    if (statement.is_null(place_of(column))) {
        return nullopt;
    }

    return Clock::time_point(milliseconds(statement.integer(place_of(column))));
}

// The size the row statement is on holds, if any.
optional<size_t> optional_size(const Statement& statement) {
    if (statement.is_null(place_of(Column::Size))) {
        return nullopt;
    }

    return static_cast<size_t>(statement.integer(place_of(Column::Size)));
}

} // namespace

namespace stats {

ContentUses::ContentUses(Database& database, Now now)
    : m_database(&database)
    , m_now(std::move(now)) {
    if (!m_now) {
        throw invalid_argument("ContentUses needs a clock");
    }
}

void ContentUses::record_stored(const Identifier& identifier, size_t size) {
    Statement statement(*m_database, stored_sql);

    statement.bind(identifier.to_string()).bind(static_cast<int64_t>(size));
    statement.bind(milliseconds_of(m_now()));
    (void)statement.step();
}

void ContentUses::record_requested(const Identifier& identifier) {
    Statement statement(*m_database, requested_sql);

    statement.bind(identifier.to_string()).bind(milliseconds_of(m_now()));
    (void)statement.step();
}

optional<Uses> ContentUses::uses(const Identifier& identifier) const {
    Statement statement(*m_database, uses_sql);

    statement.bind(identifier.to_string());

    if (!statement.step()) {
        return nullopt;
    }

    return Uses{
        optional_size(statement),
        optional_time(statement, Column::LastStored),
        optional_time(statement, Column::LastRequested),
        statement.integer(place_of(Column::Requests)),
    };
}

} // namespace stats
