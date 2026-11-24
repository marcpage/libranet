#include "Sought.hpp"

#include <chrono>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "db/Database.hpp"
#include "db/Statement.hpp"
#include "protocol/SeekList.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"

using db::Database;
using db::Statement;
using protocol::SeekList;
using stats::Sought;
using std::int64_t;
using std::invalid_argument;
using std::string;
using std::string_view;
using std::to_string;
using std::vector;
using std::chrono::duration_cast;
using std::chrono::milliseconds;
using storage::HashPrefix;
using storage::Identifier;

namespace {

using Clock = Sought::Clock;

// The kinds of entry, as the seek list's keys name them (HttpApi §10.7.1).
constexpr string_view data_kind = "data";
constexpr string_view search_kind = "search";

// Whether the entry ?2 of kind ?1 was last sought after ?3, and so is
// listed.
constexpr string_view listed_sql =
    "SELECT 1 FROM sought WHERE kind = ?1 AND value = ?2 AND sought_at > ?3";

// Seeks the entry ?2 of kind ?1 from ?3.
constexpr string_view seek_sql =
    "INSERT INTO sought (kind, value, sought_at) VALUES (?1, ?2, ?3) "
    "ON CONFLICT (kind, value) DO UPDATE SET sought_at = excluded.sought_at";

// Seeks the entry ?2 of kind ?1 no more.
constexpr string_view found_sql = "DELETE FROM sought WHERE kind = ?1 AND value = ?2";

// Forgets each entry last sought at ?1 or before.
constexpr string_view expire_sql = "DELETE FROM sought WHERE sought_at <= ?1";

// The entries of kind ?1 last sought after ?2, the most recently first.
constexpr string_view entries_sql = "SELECT value FROM sought WHERE kind = ?1 AND sought_at > ?2 "
                                    "ORDER BY sought_at DESC, value";

// The milliseconds since the Unix epoch of time, as the table holds a time.
int64_t milliseconds_of(Clock::time_point time) {
    return duration_cast<milliseconds>(time.time_since_epoch()).count();
}

// When an entry last sought then, or before, is no longer listed at now:
// entry_ttl before it, as the table holds a time.
int64_t cutoff_of(Clock::time_point now, Sought::Milliseconds entry_ttl) {
    return milliseconds_of(now - entry_ttl);
}

// Whether the entry value of kind was last sought after cutoff, and so is
// listed.
bool listed(Database& database, string_view kind, string_view value, int64_t cutoff) {
    Statement statement(database, listed_sql);

    statement.bind(kind).bind(value).bind(cutoff);
    return statement.step();
}

// Seeks the entry value of kind from now, and says whether it was not
// listed before.
bool seek_entry(Database& database, string_view kind, string_view value, Clock::time_point now,
                Sought::Milliseconds entry_ttl) {
    const auto was_listed = listed(database, kind, value, cutoff_of(now, entry_ttl));
    Statement statement(database, seek_sql);

    statement.bind(kind).bind(value).bind(milliseconds_of(now));
    (void)statement.step();
    return !was_listed;
}

// The entries of kind last sought after cutoff, the most recently first,
// each read from its text with parse.
template<typename Entry, typename Parse>
vector<Entry> entries_of(Database& database, string_view kind, int64_t cutoff, Parse parse) {
    Statement statement(database, entries_sql);
    vector<Entry> entries;

    statement.bind(kind).bind(cutoff);

    while (statement.step()) {
        entries.push_back(parse(statement.text(0)));
    }

    return entries;
}

} // namespace

namespace stats {

Sought::Sought(Database& database, Milliseconds entry_ttl, ClockFunction clock)
    : m_database(&database)
    , m_entry_ttl(entry_ttl)
    , m_clock(std::move(clock)) {
    if (m_entry_ttl <= Milliseconds::zero()) {
        throw invalid_argument("stats.seek_entry_ttl_seconds must be more than zero: "
                               + to_string(m_entry_ttl.count()) + " ms");
    }

    if (!m_clock) {
        throw invalid_argument("Sought needs a clock");
    }
}

bool Sought::seek(const Identifier& identifier) {
    return seek_entry(*m_database, data_kind, identifier.to_string(), m_clock(), m_entry_ttl);
}

bool Sought::seek(const HashPrefix& prefix) {
    return seek_entry(*m_database, search_kind, prefix.to_string(), m_clock(), m_entry_ttl);
}

bool Sought::found(const Identifier& identifier) {
    const auto value = identifier.to_string();
    const auto was_listed = listed(*m_database, data_kind, value,
                                   cutoff_of(m_clock(), m_entry_ttl));
    Statement statement(*m_database, found_sql);

    statement.bind(data_kind).bind(value);
    (void)statement.step();
    return was_listed;
}

void Sought::forget_expired() {
    Statement statement(*m_database, expire_sql);

    statement.bind(cutoff_of(m_clock(), m_entry_ttl));
    (void)statement.step();
}

SeekList Sought::seek_list() const {
    const auto cutoff = cutoff_of(m_clock(), m_entry_ttl);
    auto data = entries_of<Identifier>(*m_database, data_kind, cutoff,
                                       [](const string& text) { return Identifier::from(text); });
    auto search = entries_of<HashPrefix>(*m_database, search_kind, cutoff,
                                         [](const string& text) { return HashPrefix::parse(text); });

    return SeekList(std::move(data), std::move(search));
}

} // namespace stats
