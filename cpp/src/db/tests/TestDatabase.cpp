#include <gtest/gtest.h>
#include <filesystem>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>

#include "db/Database.hpp"

using db::Database;
using db::SqliteError;

namespace {

// A table of names, each held once, made only if it is missing.
constexpr std::string_view names_schema =
    "CREATE TABLE IF NOT EXISTS names (name TEXT PRIMARY KEY)";

// A directory of the running test's own, made under the system's directory
// for temporary files, and removed with all it holds when this is destroyed.
// SQLite writes files beside the database, which go with it.
class ScratchDirectory {
public:
    ScratchDirectory();
    ~ScratchDirectory();
    ScratchDirectory(const ScratchDirectory&) = delete;
    ScratchDirectory& operator=(const ScratchDirectory&) = delete;
    ScratchDirectory(ScratchDirectory&&) = delete;
    ScratchDirectory& operator=(ScratchDirectory&&) = delete;

    // Where the database is kept in this directory.
    [[nodiscard]] std::filesystem::path database() const;

private:
    std::filesystem::path m_path;
};

ScratchDirectory::ScratchDirectory() {
    const auto* const test = testing::UnitTest::GetInstance()->current_test_info();
    std::random_device random;

    m_path = std::filesystem::temp_directory_path()
             / (std::string("libranet-") + test->test_suite_name() + "-" + test->name() + "-"
                + std::to_string(random()));
    std::filesystem::create_directories(m_path);
}

ScratchDirectory::~ScratchDirectory() {
    std::error_code error;

    std::filesystem::remove_all(m_path, error);
}

std::filesystem::path ScratchDirectory::database() const {
    return m_path / "libranet.sqlite3";
}

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

TEST(DatabaseTest, FileThatCannotBeOpenedThrows) {
    const ScratchDirectory directory;
    const auto path = directory.database().parent_path() / "missing" / "libranet.sqlite3";

    EXPECT_THROW(const Database database(path, names_schema), SqliteError);
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
