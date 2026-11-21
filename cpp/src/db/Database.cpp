#include "Database.hpp"

#include <filesystem>
#include <stdexcept>
#include <string>
#include <string_view>

#include <sqlite3.h>

namespace {

// Throws what was being done when a call on connection failed, and what
// SQLite said of it.
[[noreturn]] void throw_failure(sqlite3* connection, std::string_view what) {
    throw db::SqliteError(std::string(what) + ": " + sqlite3_errmsg(connection));
}

// A connection to the database at path, which makes the file if it is not
// there, for one thread to use. Throws SqliteError when it cannot be opened.
sqlite3* open_connection(const std::filesystem::path& path) {
    constexpr int flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_NOMUTEX;
    const auto name = path.string();
    sqlite3* connection = nullptr;

    if (SQLITE_OK != sqlite3_open_v2(name.c_str(), &connection, flags, nullptr)) {
        // SQLite makes a connection that says what failed, unless it ran out
        // of memory making one, and it is closed once it has said it.
        const auto error = "Cannot open the database " + name + ": " + sqlite3_errmsg(connection);

        (void)sqlite3_close(connection);
        throw db::SqliteError(error);
    }

    return connection;
}

// Undoes what the transaction open on connection changed. A destructor
// calls this, so a rollback that fails is not reported: one fails when no
// transaction is open, as when SQLite has rolled it back already on an
// error.
void roll_back(sqlite3* connection) noexcept {
    (void)sqlite3_exec(connection, "ROLLBACK", nullptr, nullptr, nullptr);
}

} // namespace

namespace db {

Database::Transaction::Transaction(Database& database)
    : m_database(&database) {
    m_database->execute("BEGIN");
}

Database::Transaction::~Transaction() {
    if (!m_committed) {
        roll_back(m_database->m_connection.get());
    }
}

void Database::Transaction::commit() {
    if (m_committed) {
        throw std::logic_error("Cannot commit a transaction that was committed already");
    }

    m_database->execute("COMMIT");
    m_committed = true;
}

Database::Database(const std::filesystem::path& path, std::string_view schema)
    : m_connection(open_connection(path)) {
    // Changes are written to a log beside the database before the database
    // itself, as the Python node's are, which makes each of the many small
    // transactions the node writes quicker.
    execute("PRAGMA journal_mode = WAL");

    Transaction transaction(*this);

    execute(schema);
    transaction.commit();
}

void Database::execute(std::string_view sql) {
    // SQLite reads the statements up to a null character, which a
    // std::string_view may not end with.
    const std::string statements(sql);
    const auto result =
        sqlite3_exec(m_connection.get(), statements.c_str(), nullptr, nullptr, nullptr);

    if (SQLITE_OK != result) {
        throw_failure(m_connection.get(), "Cannot run SQL");
    }
}

void Database::Closer::operator()(sqlite3* connection) const noexcept {
    // A statement not yet freed keeps the connection open until it is,
    // rather than failing to close it.
    (void)sqlite3_close_v2(connection);
}

} // namespace db
