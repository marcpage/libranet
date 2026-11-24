#pragma once

#include <filesystem>
#include <memory>
#include <stdexcept>
#include <string_view>

// Used as more than a pointer only in Database.cpp, where SQLite is included.
struct sqlite3;

namespace db {

// What was being done, and what SQLite said of it.
class SqliteError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

// A connection to an SQLite database, used by only one thread. A statement run
// outside a Transaction is a transaction of its own.
class Database {
public:
    // Undoes the changes made while it is open unless they are committed. Only
    // one is open on a database at a time.
    class Transaction {
    public:
        // Throws SqliteError when one is open on database already.
        explicit Transaction(Database& database);
        ~Transaction();

        // Throws SqliteError when SQLite cannot, and std::logic_error when
        // they were committed already.
        void commit();

        Transaction(const Transaction&) = delete;
        Transaction& operator=(const Transaction&) = delete;
        Transaction(Transaction&&) = delete;
        Transaction& operator=(Transaction&&) = delete;

    private:
        Database* m_database;
        bool m_committed = false;
    };

    // Makes the file if it is not there, and runs schema in one transaction.
    // Throws SqliteError when the file cannot be opened, or schema run.
    Database(const std::filesystem::path& path, std::string_view schema);

    // One statement or more, which take no values, and whose rows are not
    // read. Throws SqliteError when one cannot be run, and runs none after it.
    void execute(std::string_view sql);

    ~Database() = default;

    Database(const Database&) = delete;
    Database& operator=(const Database&) = delete;
    Database(Database&&) = delete;
    Database& operator=(Database&&) = delete;

private:
    friend class Statement;

    struct Closer {
        void operator()(sqlite3* connection) const noexcept;
    };

    std::unique_ptr<sqlite3, Closer> m_connection;
};

} // namespace db
