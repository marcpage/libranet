#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

// SQLite's connection and prepared statement, which only Statement.cpp
// reads, so that SQLite's header stays out of this one.
struct sqlite3;
struct sqlite3_stmt;

namespace db {

// A statement of SQL, prepared on an SQLite connection that must outlive it.
// Values are bound to its parameters in order, from the first; it steps
// through the rows it gives; and the columns of the row it is on are read
// by their index, from 0. It is finalized when it goes. Built on SQLite.
class Statement {
public:
    using Data = std::vector<std::byte>;

    // Prepares the one statement that sql holds. Throws
    // std::invalid_argument for SQL that SQLite cannot prepare, that holds
    // no statement, or that holds anything but whitespace after its first.
    Statement(sqlite3& connection, std::string_view sql);
    ~Statement() = default;
    Statement(const Statement&) = delete;
    Statement& operator=(const Statement&) = delete;
    Statement(Statement&&) noexcept = default;
    Statement& operator=(Statement&&) noexcept = default;

    // Each binds value to the next parameter, and returns this statement, so
    // that binds chain. Each throws std::out_of_range when every parameter
    // is bound already, std::logic_error once the statement has stepped
    // and is not yet reset, and std::runtime_error when SQLite cannot bind
    // the value, as when it is longer than SQLite allows.
    Statement& bind(std::int64_t value);
    // Also throws std::invalid_argument for NaN, which SQLite would hold as
    // NULL.
    Statement& bind(double value);
    Statement& bind(std::string_view value);
    Statement& bind(const Data& value);
    Statement& bind(std::nullopt_t /*null*/);
    // NULL when value is empty.
    template<typename Type>
    Statement& bind(const std::optional<Type>& value);

    // Runs the statement on to its next row: whether there is one, or false
    // once it has run to its end. Throws std::logic_error while a parameter
    // is unbound, and std::runtime_error when SQLite fails to run it, as
    // when a constraint fails.
    bool step();
    // Readies the statement to run again, with its parameters unbound.
    void reset() noexcept;

    // The value of column in the row the statement is on. Each throws
    // std::out_of_range when the statement is on no row, or its row has no
    // such column; and each but is_null() throws std::domain_error when the
    // column holds another kind of value, NULL among them.
    [[nodiscard]] bool is_null(int column) const;
    [[nodiscard]] std::int64_t integer(int column) const;
    [[nodiscard]] double real(int column) const;
    [[nodiscard]] std::string text(int column) const;
    [[nodiscard]] Data blob(int column) const;

private:
    // Finalizes a statement SQLite prepared.
    struct Finalizer {
        void operator()(sqlite3_stmt* statement) const noexcept;
    };

    std::unique_ptr<sqlite3_stmt, Finalizer> m_statement;
    // The parameter the next value is bound to: SQLite numbers them from 1.
    int m_next_parameter = 1;
};

template<typename Type>
inline Statement& Statement::bind(const std::optional<Type>& value) {
    if (value) {
        return bind(*value);
    }

    return bind(std::nullopt);
}

} // namespace db
