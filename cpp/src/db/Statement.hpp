#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "Database.hpp"

// Used as more than a pointer only in Statement.cpp, where SQLite is included.
struct sqlite3_stmt;

namespace db {

// A statement of SQL, prepared on a Database that must outlive it. Parameters
// are bound in order, and columns read by their index, from 0.
class Statement {
public:
    using Data = std::vector<std::byte>;

    // Throws SqliteError for SQL that SQLite cannot prepare, and
    // std::invalid_argument for SQL that holds other than one statement.
    Statement(Database& database, std::string_view sql);
    ~Statement() = default;
    Statement(const Statement&) = delete;
    Statement& operator=(const Statement&) = delete;
    Statement(Statement&&) noexcept = default;
    Statement& operator=(Statement&&) noexcept = default;

    // Each binds the next parameter, so that binds chain. Each throws
    // std::out_of_range when every parameter is bound already,
    // std::logic_error once the statement has stepped and is not yet reset,
    // and SqliteError when SQLite cannot bind the value.
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

    // Whether there is a next row. Throws std::logic_error while a parameter
    // is unbound, and SqliteError when SQLite fails to run it.
    bool step();
    // With its parameters unbound.
    void reset() noexcept;

    // Each throws std::out_of_range when the statement is on no row, or its
    // row has no such column; and each but is_null() throws std::domain_error
    // when the column holds another kind of value, NULL among them.
    [[nodiscard]] bool is_null(int column) const;
    [[nodiscard]] std::int64_t integer(int column) const;
    [[nodiscard]] double real(int column) const;
    [[nodiscard]] std::string text(int column) const;
    [[nodiscard]] Data blob(int column) const;

    // Its AS clause's name, or without one, a name SQLite picks, which may
    // change from one release to the next. Throws std::out_of_range for no
    // such column.
    [[nodiscard]] std::string label(int column) const;

private:
    struct Finalizer {
        void operator()(sqlite3_stmt* statement) const noexcept;
    };

    std::unique_ptr<sqlite3_stmt, Finalizer> m_statement;
    // SQLite numbers them from 1.
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
