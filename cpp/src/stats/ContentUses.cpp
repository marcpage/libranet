#include "ContentUses.hpp"

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string_view>
#include <utility>
#include <vector>

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
using std::vector;
using storage::Identifier;

namespace {

using Clock = ContentUses::Clock;
using Held = ContentUses::Held;
using Uses = ContentUses::Uses;

// The columns uses_sql and held_sql read, in the order they read them:
// held_sql reads the identifier after the rest.
enum class Column : std::uint8_t {
    Size,
    LastStored,
    LastRequested,
    Requests,
    Identifier,
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

// Records content no longer held: ?1 is its identifier.
constexpr string_view deleted_sql = "UPDATE content_uses SET size = NULL WHERE identifier = ?1";

// What is known of the content ?1.
constexpr string_view uses_sql = "SELECT size, last_stored, last_requested, requests "
                                 "FROM content_uses WHERE identifier = ?1";

// What is known of each object held, and its identifier.
constexpr string_view held_sql = "SELECT size, last_stored, last_requested, requests, identifier "
                                 "FROM content_uses WHERE size IS NOT NULL";

// Where column is in the row uses_sql reads, as Statement numbers columns.
int place_of(Column column) {
    return static_cast<int>(column);
}

// The time column holds in the row statement is on, if any.
optional<Clock::time_point> optional_time(const Statement& statement, Column column) {
    if (statement.is_null(place_of(column))) {
        return nullopt;
    }

    return statement.time(place_of(column));
}

// The size the row statement is on holds, if any.
optional<size_t> optional_size(const Statement& statement) {
    if (statement.is_null(place_of(Column::Size))) {
        return nullopt;
    }

    return static_cast<size_t>(statement.integer(place_of(Column::Size)));
}

// What the row statement is on says is known of its content.
Uses uses_in(const Statement& statement) {
    return Uses{
        optional_size(statement),
        optional_time(statement, Column::LastStored),
        optional_time(statement, Column::LastRequested),
        statement.integer(place_of(Column::Requests)),
    };
}

} // namespace

namespace stats {

ContentUses::ContentUses(Database& database, ClockFunction clock)
    : m_database(&database)
    , m_clock(std::move(clock)) {
    if (!m_clock) {
        throw invalid_argument("ContentUses needs a clock");
    }
}

void ContentUses::record_stored(const Identifier& identifier, size_t size) {
    Statement statement(*m_database, stored_sql);

    statement.bind(identifier.to_string()).bind(static_cast<int64_t>(size));
    statement.bind(m_clock());
    (void)statement.step();
}

void ContentUses::record_requested(const Identifier& identifier) {
    Statement statement(*m_database, requested_sql);

    statement.bind(identifier.to_string()).bind(m_clock());
    (void)statement.step();
}

void ContentUses::record_deleted(const Identifier& identifier) {
    Statement statement(*m_database, deleted_sql);

    statement.bind(identifier.to_string());
    (void)statement.step();
}

optional<Uses> ContentUses::uses(const Identifier& identifier) const {
    Statement statement(*m_database, uses_sql);

    statement.bind(identifier.to_string());

    if (!statement.step()) {
        return nullopt;
    }

    return uses_in(statement);
}

vector<Held> ContentUses::held() const {
    Statement statement(*m_database, held_sql);
    vector<Held> held;

    while (statement.step()) {
        auto identifier = Identifier::from(statement.text(place_of(Column::Identifier)));

        held.push_back({std::move(identifier), uses_in(statement)});
    }

    return held;
}

} // namespace stats
