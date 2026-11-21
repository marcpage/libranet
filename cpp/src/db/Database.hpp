#pragma once

#include <filesystem>
#include <memory>
#include <stdexcept>
#include <string_view>

// SQLite's connection, which only Database.cpp, where SQLite is included,
// uses as more than a pointer.
struct sqlite3;

namespace db {

// A failed SQLite call: what was being done, and what SQLite said of it.
class SqliteError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// A connection to an SQLite database, built on SQLite. Only one thread uses
// it, so SQLite does not lock it for each call. A statement run outside a
// Transaction is a transaction of its own. A Transaction refers to the
// database it is open on, so a Database is neither copied nor moved.
class Database {
public:
    // The changes made to a database while this is open: kept when it is
    // committed, and undone if it goes before that. Only one is open on a
    // database at a time.
    class Transaction {
    public:
        // Throws SqliteError when one is open on database already.
        explicit Transaction(Database& database);
        ~Transaction();
        Transaction(const Transaction&) = delete;
        Transaction& operator=(const Transaction&) = delete;
        Transaction(Transaction&&) = delete;
        Transaction& operator=(Transaction&&) = delete;

        // Keeps the changes. Throws SqliteError when SQLite cannot, and the
        // changes are undone as this goes; and std::logic_error when they
        // were committed already.
        void commit();

    private:
        Database* m_database;
        bool m_committed = false;
    };

    // Opens the database at path, making the file if it is not there, and
    // runs schema in one transaction: statements that each make a table or
    // an index only if it is missing, as CREATE TABLE IF NOT EXISTS does.
    // Throws SqliteError when the file cannot be opened, or schema run.
    Database(const std::filesystem::path& path, std::string_view schema);
    ~Database() = default;
    Database(const Database&) = delete;
    Database& operator=(const Database&) = delete;
    Database(Database&&) = delete;
    Database& operator=(Database&&) = delete;

    // Runs sql: one statement or more, which take no values, and whose rows,
    // if any, are not read. Throws SqliteError when one cannot be run, and
    // runs none of those after it.
    void execute(std::string_view sql);

private:
    // A Statement is prepared on SQLite's connection.
    friend class Statement;

    // Closes SQLite's connection.
    struct Closer {
        void operator()(sqlite3* connection) const noexcept;
    };

    std::unique_ptr<sqlite3, Closer> m_connection;
};

} // namespace db
