#include <gtest/gtest.h>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "Fixtures.hpp"
#include "db/Database.hpp"
#include "db/Statement.hpp"

using db::Database;
using db::SqliteError;
using db::Statement;
using fixtures::ScratchDirectory;
using namespace std::string_view_literals;

namespace {

// A table of nodes, each id held once, made only if it is missing.
constexpr std::string_view nodes_schema =
    "CREATE TABLE IF NOT EXISTS nodes (id TEXT UNIQUE, attempts INTEGER)";

// Whether a statement prepared on database is part way through the rows of
// a table: SQLite refuses to drop a table while one is, and a statement
// finalized is part way through none.
bool busy(Database& database) {
    database.execute("CREATE TABLE IF NOT EXISTS spare (value)");

    try {
        database.execute("DROP TABLE spare");
    } catch (const SqliteError&) {
        return true;
    }

    return false;
}

// Whether Statement turns down sql as not one statement.
bool refused(Database& database, std::string_view sql) {
    try {
        const Statement statement(database, sql);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

// What Statement says when SQLite cannot prepare sql on database, or
// nothing if it can.
std::string prepare_error(Database& database, std::string_view sql) {
    try {
        const Statement statement(database, sql);
    } catch (const SqliteError& error) {
        return error.what();
    }

    return "";
}

// What step() says when SQLite cannot run statement, or nothing if it can.
std::string step_error(Statement& statement) {
    try {
        (void)statement.step();
    } catch (const SqliteError& error) {
        return error.what();
    }

    return "";
}

// Every value a byte can hold, once each.
Statement::Data every_byte() {
    constexpr auto byte_values = 256;
    Statement::Data data;

    for (auto value = 0; value < byte_values; ++value) {
        data.push_back(static_cast<std::byte>(value));
    }

    return data;
}

} // namespace

TEST(StatementTest, StepsThroughRows) {
    constexpr std::int64_t first_attempts = 3;
    constexpr std::int64_t second_attempts = 5;
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement insert(database, "INSERT INTO nodes (id, attempts) VALUES (?, ?)");

    EXPECT_FALSE(insert.bind("first").bind(first_attempts).step());
    insert.reset();
    EXPECT_FALSE(insert.bind("second").bind(second_attempts).step());

    Statement select(database, "SELECT id, attempts FROM nodes ORDER BY id");

    ASSERT_TRUE(select.step());
    EXPECT_EQ(select.text(0), "first");
    EXPECT_EQ(select.integer(1), first_attempts);
    ASSERT_TRUE(select.step());
    EXPECT_EQ(select.text(0), "second");
    EXPECT_EQ(select.integer(1), second_attempts);
    EXPECT_FALSE(select.step());
}

TEST(StatementTest, ReadsBackEachKind) {
    constexpr std::int64_t number = -42;
    constexpr double measure = 2.5;
    const Statement::Data content{std::byte{0x00}, std::byte{0xFF}};
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement statement(database, "SELECT ?, ?, ?, ?, ?");

    statement.bind(number).bind(measure).bind("text").bind(content).bind(std::nullopt);

    ASSERT_TRUE(statement.step());
    EXPECT_EQ(statement.integer(0), number);
    EXPECT_EQ(statement.real(1), measure);
    EXPECT_EQ(statement.text(2), "text");
    EXPECT_EQ(statement.blob(3), content);
    EXPECT_FALSE(statement.is_null(0));
    EXPECT_FALSE(statement.is_null(1));
    EXPECT_FALSE(statement.is_null(2));
    EXPECT_FALSE(statement.is_null(3));
    EXPECT_TRUE(statement.is_null(4));
}

TEST(StatementTest, ReadsBackIntegersAndRealsWhole) {
    constexpr auto smallest = std::numeric_limits<std::int64_t>::min();
    constexpr auto largest = std::numeric_limits<std::int64_t>::max();
    constexpr auto lowest = std::numeric_limits<double>::lowest();
    constexpr auto tiniest = std::numeric_limits<double>::denorm_min();
    constexpr auto infinite = std::numeric_limits<double>::infinity();
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement statement(database, "SELECT ?, ?, ?, ?, ?");

    statement.bind(smallest).bind(largest).bind(lowest).bind(tiniest).bind(infinite);

    ASSERT_TRUE(statement.step());
    EXPECT_EQ(statement.integer(0), smallest);
    EXPECT_EQ(statement.integer(1), largest);
    EXPECT_EQ(statement.real(2), lowest);
    EXPECT_EQ(statement.real(3), tiniest);
    EXPECT_EQ(statement.real(4), infinite);
}

TEST(StatementTest, ReadsBackTextAndBlobsWhole) {
    constexpr auto with_nul = "before\0after"sv;
    constexpr auto unicode = "Grüße, 世界"sv;
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement statement(database, "SELECT ?, ?, ?");

    statement.bind(with_nul).bind(unicode).bind(every_byte());

    ASSERT_TRUE(statement.step());
    EXPECT_EQ(statement.text(0), with_nul);
    EXPECT_EQ(statement.text(1), unicode);
    EXPECT_EQ(statement.blob(2), every_byte());
}

TEST(StatementTest, EmptyTextAndBlobsAreNotNull) {
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement statement(database, "SELECT ?, ?, ?");

    statement.bind(std::string_view()).bind("").bind(Statement::Data());

    ASSERT_TRUE(statement.step());
    EXPECT_FALSE(statement.is_null(0));
    EXPECT_FALSE(statement.is_null(1));
    EXPECT_FALSE(statement.is_null(2));
    EXPECT_EQ(statement.text(0), "");
    EXPECT_EQ(statement.text(1), "");
    EXPECT_EQ(statement.blob(2), Statement::Data());
}

TEST(StatementTest, BindsOptionalValues) {
    constexpr std::int64_t number = 7;
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement statement(database, "SELECT ?, ?");

    statement.bind(std::optional<std::int64_t>(number)).bind(std::optional<std::string>());

    ASSERT_TRUE(statement.step());
    EXPECT_EQ(statement.integer(0), number);
    EXPECT_TRUE(statement.is_null(1));
}

TEST(StatementTest, ResetRunsAgainWithNewValues) {
    constexpr std::int64_t first = 1;
    constexpr std::int64_t second = 2;
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement statement(database, "SELECT ?");

    ASSERT_TRUE(statement.bind(first).step());
    EXPECT_EQ(statement.integer(0), first);
    statement.reset();
    ASSERT_TRUE(statement.bind(second).step());
    EXPECT_EQ(statement.integer(0), second);
    EXPECT_FALSE(statement.step());
}

TEST(StatementTest, ResetUnbindsEveryParameter) {
    constexpr std::int64_t number = 1;
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement statement(database, "SELECT ?");

    ASSERT_TRUE(statement.bind(number).step());
    statement.reset();

    EXPECT_THROW((void)statement.step(), std::logic_error);
}

TEST(StatementTest, SteppingWithAParameterUnboundIsRefused) {
    constexpr std::int64_t number = 1;
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement statement(database, "SELECT ?, ?");

    statement.bind(number);

    EXPECT_THROW((void)statement.step(), std::logic_error);
}

TEST(StatementTest, BindingPastTheLastParameterIsRefused) {
    constexpr std::int64_t number = 1;
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement none(database, "SELECT 1");
    Statement one(database, "SELECT ?");

    one.bind(number);

    EXPECT_THROW(none.bind(number), std::out_of_range);
    EXPECT_THROW(one.bind(number), std::out_of_range);
}

TEST(StatementTest, BindingOnceSteppedIsRefused) {
    constexpr std::int64_t number = 1;
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement running(database, "SELECT 1 WHERE ? IS NOT NULL");
    Statement finished(database, "SELECT 1 WHERE ? IS NULL");

    ASSERT_TRUE(running.bind(number).step());
    ASSERT_FALSE(finished.bind(number).step());

    EXPECT_THROW(running.bind(number), std::logic_error);
    EXPECT_THROW(finished.bind(number), std::logic_error);
}

TEST(StatementTest, NaNIsRefused) {
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement statement(database, "SELECT ?");

    EXPECT_THROW(statement.bind(std::numeric_limits<double>::quiet_NaN()), std::invalid_argument);
}

TEST(StatementTest, OneStatementWithWhitespaceAfterIsPrepared) {
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement statement(database, "SELECT 'one'; \t\n");

    ASSERT_TRUE(statement.step());
    EXPECT_EQ(statement.text(0), "one");
}

TEST(StatementTest, SqlThatCannotBePreparedSaysWhy) {
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);

    const auto error = prepare_error(database, "SELECT * FROM missing");

    EXPECT_EQ(error, "Cannot prepare SQL: no such table: missing");
    EXPECT_THROW(const Statement statement(database, "SELEC 1"), SqliteError);
}

TEST(StatementTest, SqlThatIsNotOneStatementIsRefused) {
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);

    EXPECT_TRUE(refused(database, std::string_view()));
    EXPECT_TRUE(refused(database, ""));
    EXPECT_TRUE(refused(database, " \n"));
    EXPECT_TRUE(refused(database, ";"));
    EXPECT_TRUE(refused(database, "-- no statement"));
    EXPECT_TRUE(refused(database, "SELECT 1; SELECT 2"));
    EXPECT_TRUE(refused(database, "SELECT 1; garbage"));
}

TEST(StatementTest, FailingToRunSaysWhy) {
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement insert(database, "INSERT INTO nodes (id) VALUES (?)");

    EXPECT_FALSE(insert.bind("taken").step());
    insert.reset();
    EXPECT_EQ(step_error(insert.bind("taken")),
              "Cannot run the statement: UNIQUE constraint failed: nodes.id");
    insert.reset();
    EXPECT_FALSE(insert.bind("free").step());
}

TEST(StatementTest, ReadingAnotherKindIsRefused) {
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement statement(database, "SELECT 1, 1.5, 'text', x'00', NULL");

    ASSERT_TRUE(statement.step());

    EXPECT_THROW((void)statement.integer(1), std::domain_error);
    EXPECT_THROW((void)statement.real(0), std::domain_error);
    EXPECT_THROW((void)statement.text(3), std::domain_error);
    EXPECT_THROW((void)statement.blob(2), std::domain_error);
    EXPECT_THROW((void)statement.integer(4), std::domain_error);
}

TEST(StatementTest, ReadingOutsideTheRowIsRefused) {
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement statement(database, "SELECT 1");

    EXPECT_THROW((void)statement.is_null(0), std::out_of_range);
    ASSERT_TRUE(statement.step());
    EXPECT_THROW((void)statement.is_null(-1), std::out_of_range);
    EXPECT_THROW((void)statement.integer(1), std::out_of_range);
    ASSERT_FALSE(statement.step());
    EXPECT_THROW((void)statement.integer(0), std::out_of_range);
}

TEST(StatementTest, LabelsItsColumns) {
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement statement(database, R"(SELECT id AS node, attempts AS "tries so far" FROM nodes)");

    EXPECT_EQ(statement.label(0), "node");
    EXPECT_EQ(statement.label(1), "tries so far");
    EXPECT_FALSE(statement.step());
    EXPECT_EQ(statement.label(0), "node");
}

TEST(StatementTest, LabelOutsideTheColumnsIsRefused) {
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);
    Statement select(database, "SELECT id AS node FROM nodes");
    Statement insert(database, "INSERT INTO nodes (id) VALUES ('first')");

    EXPECT_THROW((void)select.label(-1), std::out_of_range);
    EXPECT_THROW((void)select.label(1), std::out_of_range);
    EXPECT_THROW((void)insert.label(0), std::out_of_range);
}

TEST(StatementTest, FinalizedWhenItGoes) {
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);

    database.execute("INSERT INTO nodes (id) VALUES ('first')");

    {
        Statement statement(database, "SELECT id FROM nodes");

        ASSERT_TRUE(statement.step());
        EXPECT_TRUE(busy(database));
    }

    EXPECT_FALSE(busy(database));
}

TEST(StatementTest, MovedStatementRuns) {
    constexpr std::int64_t number = 7;
    const ScratchDirectory directory;
    Database database(directory.database(), nodes_schema);

    database.execute("INSERT INTO nodes (id) VALUES ('first')");

    Statement original(database, "SELECT ?");
    Statement moved(std::move(original));
    Statement assigned(database, "SELECT id FROM nodes");

    ASSERT_TRUE(assigned.step());
    assigned = std::move(moved);

    // The statement assigned over, part way through its rows, is finalized.
    EXPECT_FALSE(busy(database));
    ASSERT_TRUE(assigned.bind(number).step());
    EXPECT_EQ(assigned.integer(0), number);
}
