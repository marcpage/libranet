#include "Statement.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <iterator>
#include <limits>
#include <new>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

#include <sqlite3.h>

#include "Database.hpp"

using db::SqliteError;
using std::bad_alloc;
using std::byte;
using std::copy_n;
using std::distance;
using std::domain_error;
using std::int64_t;
using std::invalid_argument;
using std::isnan;
using std::logic_error;
using std::min;
using std::nullopt_t;
using std::numeric_limits;
using std::out_of_range;
using std::size_t;
using std::string;
using std::string_view;
using std::to_string;

namespace {

// A kind of value SQLite holds, as sqlite3_column_type() gives it, and its
// name, for messages.
struct Kind {
    int code = SQLITE_NULL;
    string_view name;
};

constexpr Kind integer_kind{SQLITE_INTEGER, "an integer"};
constexpr Kind real_kind{SQLITE_FLOAT, "a real"};
constexpr Kind text_kind{SQLITE_TEXT, "text"};
constexpr Kind blob_kind{SQLITE_BLOB, "a blob"};

// The most SQL SQLite is given to read, as it counts its length in an int.
// SQLite refuses SQL longer than its own limit, a billion bytes unless
// built with another, so SQL cut to this is refused as it would be whole.
constexpr auto most_sql = static_cast<size_t>(numeric_limits<int>::max());

// What SQLite last said went wrong on connection.
string error_of(sqlite3* connection) {
    return sqlite3_errmsg(connection);
}

template<typename Exception>
void validate(bool condition, string_view message = "", sqlite3* connection = nullptr) {
    if (!condition) {
        throw Exception(string(message)
                        + (nullptr == connection ? string() : error_of(connection)));
    }
}

// The one statement sql holds, prepared on connection, for the caller to
// finalize. Throws SqliteError for SQL that SQLite cannot prepare, and
// invalid_argument for SQL that holds no statement, or that holds
// anything but whitespace after its first. The return type is deduced:
// CodeQL reads the code without building it, and may take a declaration
// that starts with a type of SQLite's for a variable.
auto* prepare(sqlite3& connection, string_view sql) {
    constexpr string_view whitespace = " \t\n\v\f\r";
    const auto length = static_cast<int>(min(sql.size(), most_sql));
    // SQLite refuses SQL at no address, where empty text may be.
    const auto* text = sql.empty() ? "" : sql.data();
    sqlite3_stmt* statement = nullptr;
    const char* tail = nullptr;
    const auto result = sqlite3_prepare_v2(&connection, text, length, &statement, &tail);

    validate<SqliteError>(SQLITE_OK == result, "Cannot prepare SQL: ", &connection);
    validate<invalid_argument>(nullptr != statement, "No statement in the SQL: " + string(sql));

    // SQLite reads the first statement, and says where it ends with tail.
    const auto first_length = static_cast<size_t>(distance(text, tail));

    if (string_view::npos != sql.find_first_not_of(whitespace, first_length)) {
        (void)sqlite3_finalize(statement);
        throw invalid_argument("Only one statement can be prepared at a time: "
                               + string(sql));
    }

    return statement;
}

// Throws for what SQLite gave, result, when binding a value, unless it
// bound it: out_of_range when the statement has no parameter left,
// logic_error when it has stepped and is not yet reset, and
// SqliteError for anything else.
void check_bound(int result) {
    if (SQLITE_OK == result) {
        return;
    }

    validate<out_of_range>(SQLITE_RANGE != result,
                           "Every parameter of the statement is bound already");
    validate<logic_error>(SQLITE_MISUSE != result,
                          "Cannot bind a value to statement that has stepped until it is reset");
    throw db::SqliteError(string("Cannot bind the value: ") + sqlite3_errstr(result));
}

// The kind of value column holds in the row statement is on, as
// sqlite3_column_type() gives it. Throws out_of_range when statement is
// on no row, or its row has no such column, where what SQLite would give is
// undefined.
int kind_of(sqlite3_stmt* statement, int column) {
    const auto columns = sqlite3_data_count(statement);

    validate<out_of_range>(column >= 0 && column < columns,
                           "No column " + to_string(column) + " in the statement's row");
    return sqlite3_column_type(statement, column);
}

// Throws domain_error unless column, in the row statement is on, holds
// a value of kind, and out_of_range as kind_of() does.
void expect(sqlite3_stmt* statement, int column, Kind kind) {
    validate<domain_error>(kind.code == kind_of(statement, column),
                           "Column " + to_string(column) + " does not hold "
                               + string(kind.name));
}

} // namespace

namespace db {

Statement::Statement(Database& database, string_view sql)
    : m_statement(prepare(*database.m_connection, sql)) {}

Statement& Statement::bind(int64_t value) {
    check_bound(sqlite3_bind_int64(m_statement.get(), m_next_parameter, value));
    ++m_next_parameter;
    return *this;
}

Statement& Statement::bind(double value) {
    validate<invalid_argument>(!isnan(value), "SQLite cannot hold NaN, only NULL in its place");
    check_bound(sqlite3_bind_double(m_statement.get(), m_next_parameter, value));
    ++m_next_parameter;
    return *this;
}

Statement& Statement::bind(string_view value) {
    // SQLite binds NULL for text at no address, where empty text may be.
    const auto* characters = value.empty() ? "" : value.data();

    check_bound(sqlite3_bind_text64(m_statement.get(), m_next_parameter, characters, value.size(),
                                    SQLITE_TRANSIENT, SQLITE_UTF8));
    ++m_next_parameter;
    return *this;
}

Statement& Statement::bind(const Data& value) {
    // SQLite binds NULL for a blob at no address, where an empty one may
    // be, so an empty one is bound as a blob of no bytes.
    if (value.empty()) {
        check_bound(sqlite3_bind_zeroblob(m_statement.get(), m_next_parameter, 0));
    } else {
        check_bound(sqlite3_bind_blob64(m_statement.get(), m_next_parameter, value.data(),
                                        value.size(), SQLITE_TRANSIENT));
    }

    ++m_next_parameter;
    return *this;
}

Statement& Statement::bind(nullopt_t /*null*/) {
    check_bound(sqlite3_bind_null(m_statement.get(), m_next_parameter));
    ++m_next_parameter;
    return *this;
}

bool Statement::step() {
    validate<logic_error>(m_next_parameter > sqlite3_bind_parameter_count(m_statement.get()),
                          "Cannot run a statement before each of its parameters is bound");

    const auto result = sqlite3_step(m_statement.get());

    if (SQLITE_ROW == result) {
        return true;
    }

    if (SQLITE_DONE == result) {
        return false;
    }

    throw SqliteError("Cannot run the statement: " + error_of(sqlite3_db_handle(m_statement.get())));
}

void Statement::reset() noexcept {
    // Each gives what the last step gave, which step() has thrown already
    // if it failed.
    (void)sqlite3_reset(m_statement.get());
    (void)sqlite3_clear_bindings(m_statement.get());
    m_next_parameter = 1;
}

bool Statement::is_null(int column) const {
    return SQLITE_NULL == kind_of(m_statement.get(), column);
}

int64_t Statement::integer(int column) const {
    expect(m_statement.get(), column, integer_kind);
    return sqlite3_column_int64(m_statement.get(), column);
}

double Statement::real(int column) const {
    expect(m_statement.get(), column, real_kind);
    return sqlite3_column_double(m_statement.get(), column);
}

string Statement::text(int column) const {
    expect(m_statement.get(), column, text_kind);

    // SQLite gives text as unsigned char, which a char may be read as.
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
    const auto* characters = reinterpret_cast<const char*>(
        sqlite3_column_text(m_statement.get(), column));
    const auto size = static_cast<size_t>(sqlite3_column_bytes(m_statement.get(), column));

    return string(characters, size);
}

Statement::Data Statement::blob(int column) const {
    expect(m_statement.get(), column, blob_kind);

    const auto* bytes = static_cast<const byte*>(sqlite3_column_blob(m_statement.get(), column));
    const auto size = static_cast<size_t>(sqlite3_column_bytes(m_statement.get(), column));
    Data data(size);

    copy_n(bytes, size, data.begin());
    return data;
}

string Statement::label(int column) const {
    validate<out_of_range>(column >= 0 && column < sqlite3_column_count(m_statement.get()),
                           "No column " + to_string(column) + " in the statement's rows");

    const auto* name = sqlite3_column_name(m_statement.get(), column);

    // SQLite gives no name only when it ran out of memory making one.
    if (nullptr == name) {
        throw bad_alloc();
    }

    return name;
}

void Statement::Finalizer::operator()(sqlite3_stmt* statement) const noexcept {
    // It gives what the last step gave, which step() has thrown already if
    // it failed.
    (void)sqlite3_finalize(statement);
}

} // namespace db
