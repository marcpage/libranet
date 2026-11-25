#include <gtest/gtest.h>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <string_view>

#include "Fixtures.hpp"
#include "db/Database.hpp"

using db::Database;
using db::SqliteError;
using fixtures::ScratchDirectory;

namespace {

// A table of names, each held once, made only if it is missing.
constexpr std::string_view names_schema =
    "CREATE TABLE IF NOT EXISTS names (name TEXT PRIMARY KEY)";

// Whether name could be added to the names table in database, as it can be
// only when the table is there and does not hold name already.
bool adds(Database& database, std::string_view name) {
    try {
        database.execute("INSERT INTO names (name) VALUES ('" + std::string(name) + "')");
    } catch (const SqliteError&) {
        return false;
    }

    return true;
}

// What Database::execute() says when it cannot run sql on database, or
// nothing if it can.
std::string execute_error(Database& database, std::string_view sql) {
    try {
        database.execute(sql);
    } catch (const SqliteError& error) {
        return error.what();
    }

    return "";
}

// What Database's constructor says when it cannot open the database at
// path, or nothing if it can.
std::string open_error(const std::filesystem::path& path) {
    try {
        (void)Database(path, names_schema);
    } catch (const SqliteError& error) {
        return error.what();
    }

    return "";
}

} // namespace

TEST(DatabaseTest, MakesTheFile) {
    const ScratchDirectory directory;

    (void)Database(directory.database(), names_schema);

    EXPECT_TRUE(std::filesystem::exists(directory.database()));
}

TEST(DatabaseTest, MakesTheTablesOfItsSchema) {
    const ScratchDirectory directory;

    Database database(directory.database(), names_schema);

    EXPECT_TRUE(adds(database, "alice"));
}

TEST(DatabaseTest, KeepsWhatItHoldsWhenOpenedAgain) {
    const ScratchDirectory directory;

    {
        Database database(directory.database(), names_schema);

        ASSERT_TRUE(adds(database, "alice"));
    }

    Database database(directory.database(), names_schema);

    EXPECT_FALSE(adds(database, "alice"));
    EXPECT_TRUE(adds(database, "bob"));
}

// As the Python node's database does, changes go to a log beside the
// database first.
TEST(DatabaseTest, WritesAheadToALog) {
    const ScratchDirectory directory;
    Database database(directory.database(), names_schema);

    ASSERT_TRUE(adds(database, "alice"));

    EXPECT_TRUE(std::filesystem::exists(directory.database().string() + "-wal"));
}

TEST(DatabaseTest, FileThatCannotBeOpenedSaysWhy) {
    const ScratchDirectory directory;
    const auto path = directory.database().parent_path() / "missing" / "libranet.sqlite3";

    const auto error = open_error(path);

    EXPECT_EQ(error,
              "Cannot open the database " + path.string() + ": unable to open database file");
}

TEST(DatabaseTest, SchemaThatCannotBeRunThrows) {
    const ScratchDirectory directory;

    EXPECT_THROW(const Database database(directory.database(), "CREATE TABLE names ("),
                 SqliteError);
}

TEST(DatabaseTest, SchemaIsMadeWholeOrNotAtAll) {
    const ScratchDirectory directory;
    constexpr std::string_view half_made =
        "CREATE TABLE names (name TEXT PRIMARY KEY); CREATE TABLE others (";

    EXPECT_THROW(const Database database(directory.database(), half_made), SqliteError);

    Database database(directory.database(), "");

    // The names table, made before the statement that failed, is not there.
    EXPECT_FALSE(adds(database, "alice"));
}

TEST(DatabaseTest, StatementThatCannotBeRunSaysWhy) {
    const ScratchDirectory directory;
    Database database(directory.database(), names_schema);

    const auto error = execute_error(database, "INSERT INTO missing (name) VALUES ('alice')");

    EXPECT_EQ(error, "Cannot run SQL: no such table: missing");
}

TEST(DatabaseTest, ChangesCommittedAreKept) {
    const ScratchDirectory directory;
    Database database(directory.database(), names_schema);

    {
        Database::Transaction transaction(database);

        ASSERT_TRUE(adds(database, "alice"));
        transaction.commit();
    }

    EXPECT_FALSE(adds(database, "alice"));
}

TEST(DatabaseTest, ChangesNotCommittedAreUndone) {
    const ScratchDirectory directory;
    Database database(directory.database(), names_schema);

    {
        const Database::Transaction transaction(database);

        ASSERT_TRUE(adds(database, "alice"));
    }

    EXPECT_TRUE(adds(database, "alice"));
}

TEST(DatabaseTest, OnlyOneTransactionIsOpenAtATime) {
    const ScratchDirectory directory;
    Database database(directory.database(), names_schema);
    const Database::Transaction transaction(database);

    EXPECT_THROW(const Database::Transaction inner(database), SqliteError);
}

TEST(DatabaseTest, TransactionIsCommittedOnce) {
    const ScratchDirectory directory;
    Database database(directory.database(), names_schema);
    Database::Transaction transaction(database);

    transaction.commit();

    EXPECT_THROW(transaction.commit(), std::logic_error);
}

TEST(DatabaseTest, CommitThatFailsThrows) {
    const ScratchDirectory directory;
    Database database(directory.database(), names_schema);
    Database::Transaction transaction(database);

    // Ended behind its back, which leaves SQLite no transaction to commit.
    database.execute("ROLLBACK");

    EXPECT_THROW(transaction.commit(), SqliteError);
}
