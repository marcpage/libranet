#include "NodeAddresses.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "db/Database.hpp"
#include "db/Statement.hpp"
#include "protocol/NodeList.hpp"
#include "storage/Identifier.hpp"

using db::Database;
using db::Statement;
using protocol::NodeList;
using stats::NodeAddresses;
using std::array;
using std::find_if;
using std::int64_t;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::string;
using std::string_view;
using std::to_string;
using std::vector;
using storage::Identifier;

namespace {

using Address = NodeAddresses::Address;
using Candidate = NodeAddresses::Candidate;
using Clock = NodeAddresses::Clock;
using Source = NodeAddresses::Source;

// Every source, weakest first, as Source lists them.
constexpr array all_sources = {
    Source::Relayed,
    Source::Advertised,
    Source::Observed,
    Source::Dialed,
};

// The columns addresses_sql() reads, in the order it reads them.
enum class Column : std::uint8_t {
    Endpoint,
    SourceName,
    LastLearned,
    FirstSuccess,
    LastSuccess,
    Attempts,
    ConsecutiveFailures,
};

// The order a node's addresses are tried in: those that have worked, the
// most recently first, then the rest, the most recently learned first.
constexpr string_view try_order =
    "last_success IS NULL, last_success DESC, last_learned DESC, endpoint";

// Records an address learned: ?1 is its node id, ?2 its endpoint, ?3 its
// source, and ?4 when. learn_sql() ends it with the source to keep.
constexpr string_view learn_insert =
    "INSERT INTO node_addresses (node_id, endpoint, source, last_learned) "
    "VALUES (?1, ?2, ?3, ?4) "
    "ON CONFLICT (node_id, endpoint) DO UPDATE SET last_learned = excluded.last_learned, "
    "source = ";

// Records an address that worked: ?1 is its node id, ?2 its endpoint, ?3
// the source Dialed, and ?4 when.
constexpr string_view success_sql =
    "INSERT INTO node_addresses (node_id, endpoint, source, last_learned, first_success, "
    "last_success, attempts) VALUES (?1, ?2, ?3, ?4, ?4, ?4, 1) "
    "ON CONFLICT (node_id, endpoint) DO UPDATE SET source = excluded.source, "
    "first_success = COALESCE(first_success, excluded.first_success), "
    "last_success = excluded.last_success, attempts = attempts + 1, consecutive_failures = 0";

// Records an address that failed: ?1 is its node id, and ?2 its endpoint.
constexpr string_view failure_sql =
    "UPDATE node_addresses SET attempts = attempts + 1, "
    "consecutive_failures = consecutive_failures + 1 WHERE node_id = ?1 AND endpoint = ?2";

// Drops the address ?2 of the node ?1 if it has never worked and has failed
// ?3 times in a row, or more.
constexpr string_view drop_sql =
    "DELETE FROM node_addresses WHERE node_id = ?1 AND endpoint = ?2 "
    "AND last_success IS NULL AND consecutive_failures >= ?3";

// Each node's last address that worked, unless it has failed since, the
// most recently reached first, but for the node ?1.
constexpr string_view node_list_sql =
    "SELECT endpoint, node_id FROM (SELECT endpoint, node_id, last_success, "
    "consecutive_failures, ROW_NUMBER() OVER (PARTITION BY node_id "
    "ORDER BY last_success DESC, endpoint) AS place FROM node_addresses "
    "WHERE last_success IS NOT NULL AND node_id <> ?1) "
    "WHERE place = 1 AND consecutive_failures = 0 ORDER BY last_success DESC, endpoint";

// Where column is in the rows addresses_sql() reads, as Statement numbers
// columns.
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

// The name a source is written as in the table.
string_view name_of(Source source) {
    switch (source) {
        case Source::Relayed:
            return "relayed";
        case Source::Advertised:
            return "advertised";
        case Source::Observed:
            return "observed";
        case Source::Dialed:
            return "dialed";
    }

    throw invalid_argument("Unknown address source: " + to_string(static_cast<int>(source)));
}

// The source the table names by name. Throws invalid_argument for a name
// that is none.
Source source_named(string_view name) {
    const auto* const found = find_if(all_sources.begin(), all_sources.end(),
                                      [name](Source source) { return name_of(source) == name; });

    if (all_sources.end() == found) {
        throw invalid_argument("Unknown address source in the table: " + string(name));
    }

    return *found;
}

// SQL for the rank of the source that column names, weakest lowest, as
// Source orders them, so that a source added to it ranks with no other
// change.
string rank_of(string_view column) {
    string cases;

    for (const auto source : all_sources) {
        cases += " WHEN '" + string(name_of(source)) + "' THEN "
                 + to_string(static_cast<int>(source));
    }

    return "CASE " + string(column) + cases + " END";
}

// SQL that records an address learned, as learn_insert does, keeping the
// stronger of the source it has and the one it was learned from again.
string learn_sql() {
    return string(learn_insert) + "CASE WHEN " + rank_of("excluded.source") + " > "
           + rank_of("source") + " THEN excluded.source ELSE source END";
}

// SQL for each address of the node ?1, in the order they are tried.
string addresses_sql() {
    return "SELECT endpoint, source, last_learned, first_success, last_success, attempts, "
           "consecutive_failures FROM node_addresses WHERE node_id = ?1 ORDER BY "
           + string(try_order);
}

// SQL for the addresses of each node but ?1 with an address that has
// failed fewer than ?2 times in a row: those of nodes that have been
// reached, the most recently first, then the rest, the most recently
// learned first, and each node's in the order they are tried.
string candidates_sql() {
    return "SELECT node_id, endpoint FROM (SELECT node_id, endpoint, last_success, "
           "last_learned, MAX(last_success) OVER node AS node_success, "
           "MAX(last_learned) OVER node AS node_learned, "
           "MIN(consecutive_failures) OVER node AS node_failures FROM node_addresses "
           "WHERE node_id <> ?1 WINDOW node AS (PARTITION BY node_id)) "
           "WHERE node_failures < ?2 ORDER BY node_success IS NULL, node_success DESC, "
           "node_learned DESC, node_id, "
           + string(try_order);
}

// The address in the row statement is on, as addresses_sql() reads it.
Address address_of(const Statement& statement) {
    return Address{
        statement.text(place_of(Column::Endpoint)),
        source_named(statement.text(place_of(Column::SourceName))),
        statement.time(place_of(Column::LastLearned)),
        optional_time(statement, Column::FirstSuccess),
        optional_time(statement, Column::LastSuccess),
        statement.integer(place_of(Column::Attempts)),
        statement.integer(place_of(Column::ConsecutiveFailures)),
    };
}

// Throws invalid_argument for an endpoint that is not one, as NodeList
// does.
void check_endpoint(const Identifier& node_id, string_view endpoint) {
    (void)NodeList(node_id, {string(endpoint)});
}

// Throws invalid_argument for a limit less than 1, naming the setting.
void check_limit(int64_t limit, string_view setting) {
    if (limit < 1) {
        throw invalid_argument(string(setting) + " must be at least 1: " + to_string(limit));
    }
}

} // namespace

namespace stats {

NodeAddresses::NodeAddresses(Database& database, Limits limits, ClockFunction clock)
    : m_database(&database)
    , m_limits(limits)
    , m_clock(std::move(clock)) {
    check_limit(m_limits.max_address_failures, "stats.max_address_failures");
    check_limit(m_limits.max_node_failures, "stats.max_node_failures");

    if (!m_clock) {
        throw invalid_argument("NodeAddresses needs a clock");
    }
}

void NodeAddresses::record_learned(const NodeList& list, Source source) {
    const auto now = m_clock();
    Statement statement(*m_database, learn_sql());

    for (const auto& entry : list.entries()) {
        statement.bind(entry.node_id.to_string()).bind(entry.endpoint).bind(name_of(source));
        statement.bind(now);
        (void)statement.step();
        statement.reset();
    }
}

void NodeAddresses::record_success(const Identifier& node_id, string_view endpoint) {
    check_endpoint(node_id, endpoint);

    Statement statement(*m_database, success_sql);

    statement.bind(node_id.to_string()).bind(endpoint).bind(name_of(Source::Dialed));
    statement.bind(m_clock());
    (void)statement.step();
}

void NodeAddresses::record_failure(const Identifier& node_id, string_view endpoint) {
    const auto identifier = node_id.to_string();
    Statement failure(*m_database, failure_sql);
    Statement drop(*m_database, drop_sql);

    failure.bind(identifier).bind(endpoint);
    (void)failure.step();
    drop.bind(identifier).bind(endpoint).bind(m_limits.max_address_failures);
    (void)drop.step();
}

vector<Address> NodeAddresses::addresses(const Identifier& node_id) const {
    Statement statement(*m_database, addresses_sql());
    vector<Address> found;

    statement.bind(node_id.to_string());

    while (statement.step()) {
        found.push_back(address_of(statement));
    }

    return found;
}

NodeList NodeAddresses::node_list(const Identifier& own_id,
                                  const vector<string>& own_endpoints) const {
    NodeList list(own_id, own_endpoints);
    Statement statement(*m_database, node_list_sql);

    statement.bind(own_id.to_string());

    while (statement.step()) {
        list.add(statement.text(0), Identifier::parse(statement.text(1)));
    }

    return list;
}

vector<Candidate> NodeAddresses::candidates(const Identifier& own_id) const {
    Statement statement(*m_database, candidates_sql());
    vector<Candidate> found;
    string last_id; // Of the node whose addresses were read last

    statement.bind(own_id.to_string()).bind(m_limits.max_node_failures);

    while (statement.step()) {
        auto node_id = statement.text(0);

        if (found.empty() || node_id != last_id) {
            found.push_back(Candidate{Identifier::parse(node_id), {}});
            last_id = std::move(node_id);
        }

        found.back().endpoints.push_back(statement.text(1));
    }

    return found;
}

} // namespace stats
