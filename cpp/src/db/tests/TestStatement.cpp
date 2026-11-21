#include <gtest/gtest.h>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include <sqlite3.h>

#include "db/Statement.hpp"

using db::Statement;
using namespace std::string_view_literals;

namespace {

// Closes a connection to SQLite.
struct Closer {
    void operator()(sqlite3* connection) const noexcept {
        (void)sqlite3_close(connection);
    }
};

// A connection to SQLite, closed when it goes.
using Connection = std::unique_ptr<sqlite3, Closer>;

// A connection to a new database in memory, which goes when it closes.
Connection open_in_memory() {
    sqlite3* connection = nullptr;

    (void)sqlite3_open(":memory:", &connection);
    return Connection(connection);
}

// Runs sql, which takes no values and gives no rows, on connection.
void run(sqlite3& connection, std::string_view sql) {
    (void)Statement(connection, sql).step();
}

// How many statements are prepared on connection, and not yet finalized.
int prepared_on(sqlite3& connection) {
    auto count = 0;

    for (auto* statement = sqlite3_next_stmt(&connection, nullptr); nullptr != statement;
         statement = sqlite3_next_stmt(&connection, statement)) {
        ++count;
    }

    return count;
}

// Whether Statement turns down sql as invalid.
bool refused(sqlite3& connection, std::string_view sql) {
    try {
        const Statement statement(connection, sql);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
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
    const auto database = open_in_memory();

    run(*database, "CREATE TABLE nodes (id TEXT, attempts INTEGER)");

    Statement insert(*database, "INSERT INTO nodes (id, attempts) VALUES (?, ?)");

    EXPECT_FALSE(insert.bind("first").bind(first_attempts).step());
    insert.reset();
    EXPECT_FALSE(insert.bind("second").bind(second_attempts).step());

    Statement select(*database, "SELECT id, attempts FROM nodes ORDER BY id");

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
    const auto database = open_in_memory();
    Statement statement(*database, "SELECT ?, ?, ?, ?, ?");

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
    const auto database = open_in_memory();
    Statement statement(*database, "SELECT ?, ?, ?, ?, ?");

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
    const auto database = open_in_memory();
    Statement statement(*database, "SELECT ?, ?, ?");

    statement.bind(with_nul).bind(unicode).bind(every_byte());

    ASSERT_TRUE(statement.step());
    EXPECT_EQ(statement.text(0), with_nul);
    EXPECT_EQ(statement.text(1), unicode);
    EXPECT_EQ(statement.blob(2), every_byte());
}

TEST(StatementTest, EmptyTextAndBlobsAreNotNull) {
    const auto database = open_in_memory();
    Statement statement(*database, "SELECT ?, ?, ?");

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
    const auto database = open_in_memory();
    Statement statement(*database, "SELECT ?, ?");

    statement.bind(std::optional<std::int64_t>(number)).bind(std::optional<std::string>());

    ASSERT_TRUE(statement.step());
    EXPECT_EQ(statement.integer(0), number);
    EXPECT_TRUE(statement.is_null(1));
}

TEST(StatementTest, ResetRunsAgainWithNewValues) {
    constexpr std::int64_t first = 1;
    constexpr std::int64_t second = 2;
    const auto database = open_in_memory();
    Statement statement(*database, "SELECT ?");

    ASSERT_TRUE(statement.bind(first).step());
    EXPECT_EQ(statement.integer(0), first);
    statement.reset();
    ASSERT_TRUE(statement.bind(second).step());
    EXPECT_EQ(statement.integer(0), second);
    EXPECT_FALSE(statement.step());
}

TEST(StatementTest, ResetUnbindsEveryParameter) {
    constexpr std::int64_t number = 1;
    const auto database = open_in_memory();
    Statement statement(*database, "SELECT ?");

    ASSERT_TRUE(statement.bind(number).step());
    statement.reset();

    EXPECT_THROW((void)statement.step(), std::logic_error);
}

TEST(StatementTest, SteppingWithAParameterUnboundIsRefused) {
    constexpr std::int64_t number = 1;
    const auto database = open_in_memory();
    Statement statement(*database, "SELECT ?, ?");

    statement.bind(number);

    EXPECT_THROW((void)statement.step(), std::logic_error);
}

TEST(StatementTest, BindingPastTheLastParameterIsRefused) {
    constexpr std::int64_t number = 1;
    const auto database = open_in_memory();
    Statement none(*database, "SELECT 1");
    Statement one(*database, "SELECT ?");

    one.bind(number);

    EXPECT_THROW(none.bind(number), std::out_of_range);
    EXPECT_THROW(one.bind(number), std::out_of_range);
}

TEST(StatementTest, BindingOnceSteppedIsRefused) {
    constexpr std::int64_t number = 1;
    const auto database = open_in_memory();
    Statement running(*database, "SELECT 1 WHERE ? IS NOT NULL");
    Statement finished(*database, "SELECT 1 WHERE ? IS NULL");

    ASSERT_TRUE(running.bind(number).step());
    ASSERT_FALSE(finished.bind(number).step());

    EXPECT_THROW(running.bind(number), std::logic_error);
    EXPECT_THROW(finished.bind(number), std::logic_error);
}

TEST(StatementTest, NaNIsRefused) {
    const auto database = open_in_memory();
    Statement statement(*database, "SELECT ?");

    EXPECT_THROW(statement.bind(std::numeric_limits<double>::quiet_NaN()), std::invalid_argument);
}

TEST(StatementTest, ValueLongerThanSQLiteAllowsIsRefused) {
    constexpr auto most_length = 100;
    const auto database = open_in_memory();
    Statement statement(*database, "SELECT ?, ?");

    (void)sqlite3_limit(database.get(), SQLITE_LIMIT_LENGTH, most_length);

    const std::string text(most_length + 1, 'x');
    const Statement::Data content(most_length + 1);

    EXPECT_THROW(statement.bind(text), std::runtime_error);
    EXPECT_THROW(statement.bind(content), std::runtime_error);
}

TEST(StatementTest, OneStatementWithWhitespaceAfterIsPrepared) {
    const auto database = open_in_memory();
    Statement statement(*database, "SELECT 'one'; \t\n");

    ASSERT_TRUE(statement.step());
    EXPECT_EQ(statement.text(0), "one");
}

TEST(StatementTest, SqlThatIsNotOneStatementIsRefused) {
    const auto database = open_in_memory();

    EXPECT_TRUE(refused(*database, "SELEC 1"));
    EXPECT_TRUE(refused(*database, "SELECT * FROM missing"));
    EXPECT_TRUE(refused(*database, std::string_view()));
    EXPECT_TRUE(refused(*database, ""));
    EXPECT_TRUE(refused(*database, " \n"));
    EXPECT_TRUE(refused(*database, ";"));
    EXPECT_TRUE(refused(*database, "-- no statement"));
    EXPECT_TRUE(refused(*database, "SELECT 1; SELECT 2"));
    EXPECT_TRUE(refused(*database, "SELECT 1; garbage"));
    EXPECT_EQ(prepared_on(*database), 0);
}

TEST(StatementTest, FailingToRunThrows) {
    constexpr std::int64_t taken = 1;
    constexpr std::int64_t free = 2;
    const auto database = open_in_memory();

    run(*database, "CREATE TABLE nodes (id INTEGER UNIQUE)");

    Statement insert(*database, "INSERT INTO nodes (id) VALUES (?)");

    EXPECT_FALSE(insert.bind(taken).step());
    insert.reset();
    EXPECT_THROW((void)insert.bind(taken).step(), std::runtime_error);
    insert.reset();
    EXPECT_FALSE(insert.bind(free).step());
}

TEST(StatementTest, ReadingAnotherKindIsRefused) {
    const auto database = open_in_memory();
    Statement statement(*database, "SELECT 1, 1.5, 'text', x'00', NULL");

    ASSERT_TRUE(statement.step());

    EXPECT_THROW((void)statement.integer(1), std::domain_error);
    EXPECT_THROW((void)statement.real(0), std::domain_error);
    EXPECT_THROW((void)statement.text(3), std::domain_error);
    EXPECT_THROW((void)statement.blob(2), std::domain_error);
    EXPECT_THROW((void)statement.integer(4), std::domain_error);
}

TEST(StatementTest, ReadingOutsideTheRowIsRefused) {
    const auto database = open_in_memory();
    Statement statement(*database, "SELECT 1");

    EXPECT_THROW((void)statement.is_null(0), std::out_of_range);
    ASSERT_TRUE(statement.step());
    EXPECT_THROW((void)statement.is_null(-1), std::out_of_range);
    EXPECT_THROW((void)statement.integer(1), std::out_of_range);
    ASSERT_FALSE(statement.step());
    EXPECT_THROW((void)statement.integer(0), std::out_of_range);
}

TEST(StatementTest, FinalizedWhenItGoes) {
    const auto database = open_in_memory();
    auto statement = std::make_optional<Statement>(*database, "SELECT 1");

    EXPECT_EQ(prepared_on(*database), 1);
    statement = std::nullopt;
    EXPECT_EQ(prepared_on(*database), 0);
}

TEST(StatementTest, MovedStatementRuns) {
    constexpr std::int64_t number = 7;
    const auto database = open_in_memory();
    Statement original(*database, "SELECT ?");
    Statement moved(std::move(original));
    Statement assigned(*database, "SELECT 1");

    assigned = std::move(moved);

    EXPECT_EQ(prepared_on(*database), 1);
    ASSERT_TRUE(assigned.bind(number).step());
    EXPECT_EQ(assigned.integer(0), number);
}
