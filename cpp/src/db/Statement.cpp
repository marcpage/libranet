#include "Statement.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <iterator>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

#include <sqlite3.h>

namespace {

// A kind of value SQLite holds, as sqlite3_column_type() gives it, and its
// name, for messages.
struct Kind {
    int code = SQLITE_NULL;
    std::string_view name;
};

constexpr Kind integer_kind{SQLITE_INTEGER, "an integer"};
constexpr Kind real_kind{SQLITE_FLOAT, "a real"};
constexpr Kind text_kind{SQLITE_TEXT, "text"};
constexpr Kind blob_kind{SQLITE_BLOB, "a blob"};

// The most SQL SQLite is given to read, as it counts its length in an int.
// SQLite refuses SQL longer than its own limit, a billion bytes unless
// built with another, so SQL cut to this is refused as it would be whole.
constexpr auto most_sql = static_cast<std::size_t>(std::numeric_limits<int>::max());

// What SQLite last said went wrong on connection.
std::string error_of(sqlite3* connection) {
    return sqlite3_errmsg(connection);
}

// The one statement sql holds, prepared on connection, for the caller to
// finalize. Throws std::invalid_argument for SQL that SQLite cannot
// prepare, that holds no statement, or that holds anything but whitespace
// after its first. The return type is deduced: CodeQL reads the code
// without building it, and may take a declaration that starts with a type
// of SQLite's for a variable.
auto* prepare(sqlite3& connection, std::string_view sql) {
    constexpr std::string_view whitespace = " \t\n\v\f\r";
    const auto length = static_cast<int>(std::min(sql.size(), most_sql));
    // SQLite refuses SQL at no address, where empty text may be.
    const auto* text = sql.empty() ? "" : sql.data();
    sqlite3_stmt* statement = nullptr;
    const char* tail = nullptr;

    if (SQLITE_OK != sqlite3_prepare_v2(&connection, text, length, &statement, &tail)) {
        throw std::invalid_argument("SQLite cannot prepare the statement: " + error_of(&connection));
    }

    if (nullptr == statement) {
        throw std::invalid_argument("No statement in the SQL: " + std::string(sql));
    }

    // SQLite reads the first statement, and says where it ends with tail.
    const auto first_length = static_cast<std::size_t>(std::distance(text, tail));

    if (std::string_view::npos != sql.find_first_not_of(whitespace, first_length)) {
        (void)sqlite3_finalize(statement);
        throw std::invalid_argument("Only one statement can be prepared at a time: "
                                    + std::string(sql));
    }

    return statement;
}

// Throws for what SQLite gave, result, when binding a value, unless it
// bound it: std::out_of_range when the statement has no parameter left,
// std::logic_error when it has stepped and is not yet reset, and
// std::runtime_error for anything else.
void check_bound(int result) {
    if (SQLITE_OK == result) {
        return;
    }

    if (SQLITE_RANGE == result) {
        throw std::out_of_range("Every parameter of the statement is bound already");
    }

    if (SQLITE_MISUSE == result) {
        throw std::logic_error("Cannot bind a value to a statement that has stepped until it is reset");
    }

    throw std::runtime_error(std::string("SQLite cannot bind the value: ") + sqlite3_errstr(result));
}

// The kind of value column holds in the row statement is on, as
// sqlite3_column_type() gives it. Throws std::out_of_range when statement is
// on no row, or its row has no such column, where what SQLite would give is
// undefined.
int kind_of(sqlite3_stmt* statement, int column) {
    if (column < 0 || sqlite3_data_count(statement) <= column) {
        throw std::out_of_range("No column " + std::to_string(column) + " in the statement's row");
    }

    return sqlite3_column_type(statement, column);
}

// Throws std::domain_error unless column, in the row statement is on, holds
// a value of kind, and std::out_of_range as kind_of() does.
void expect(sqlite3_stmt* statement, int column, Kind kind) {
    if (kind.code != kind_of(statement, column)) {
        throw std::domain_error("Column " + std::to_string(column) + " does not hold "
                                + std::string(kind.name));
    }
}

} // namespace

namespace db {

Statement::Statement(sqlite3& connection, std::string_view sql)
    : m_statement(prepare(connection, sql)) {}

Statement& Statement::bind(std::int64_t value) {
    check_bound(sqlite3_bind_int64(m_statement.get(), m_next_parameter, value));
    ++m_next_parameter;
    return *this;
}

Statement& Statement::bind(double value) {
    if (std::isnan(value)) {
        throw std::invalid_argument("SQLite cannot hold NaN, only NULL in its place");
    }

    check_bound(sqlite3_bind_double(m_statement.get(), m_next_parameter, value));
    ++m_next_parameter;
    return *this;
}

Statement& Statement::bind(std::string_view value) {
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

Statement& Statement::bind(std::nullopt_t /*null*/) {
    check_bound(sqlite3_bind_null(m_statement.get(), m_next_parameter));
    ++m_next_parameter;
    return *this;
}

bool Statement::step() {
    if (m_next_parameter <= sqlite3_bind_parameter_count(m_statement.get())) {
        throw std::logic_error("Cannot run a statement before each of its parameters is bound");
    }

    const auto result = sqlite3_step(m_statement.get());

    if (SQLITE_ROW == result) {
        return true;
    }

    if (SQLITE_DONE == result) {
        return false;
    }

    throw std::runtime_error("SQLite cannot run the statement: "
                             + error_of(sqlite3_db_handle(m_statement.get())));
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

std::int64_t Statement::integer(int column) const {
    expect(m_statement.get(), column, integer_kind);
    return sqlite3_column_int64(m_statement.get(), column);
}

double Statement::real(int column) const {
    expect(m_statement.get(), column, real_kind);
    return sqlite3_column_double(m_statement.get(), column);
}

std::string Statement::text(int column) const {
    expect(m_statement.get(), column, text_kind);

    // SQLite gives text as unsigned char, which a char may be read as.
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
    const auto* characters = reinterpret_cast<const char*>(
        sqlite3_column_text(m_statement.get(), column));
    const auto size = static_cast<std::size_t>(sqlite3_column_bytes(m_statement.get(), column));

    return std::string(characters, size);
}

Statement::Data Statement::blob(int column) const {
    expect(m_statement.get(), column, blob_kind);

    const auto* bytes = static_cast<const std::byte*>(sqlite3_column_blob(m_statement.get(), column));
    const auto size = static_cast<std::size_t>(sqlite3_column_bytes(m_statement.get(), column));
    Data data(size);

    std::copy_n(bytes, size, data.begin());
    return data;
}

void Statement::Finalizer::operator()(sqlite3_stmt* statement) const noexcept {
    // It gives what the last step gave, which step() has thrown already if
    // it failed.
    (void)sqlite3_finalize(statement);
}

} // namespace db
