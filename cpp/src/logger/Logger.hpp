#pragma once

#include <cstdint>
#include <exception>
#include <string>
#include <string_view>
#include <type_traits>

#include "ILogFormatter.hpp"
#include "ILogOutput.hpp"

namespace logger {

class Logger {
public:
    // From the most severe to the most detailed.
    enum class Level : std::uint8_t {
        Fatal,
        Log,
        Error,
        Warning,
        Info,
        Debug,
        Verbose,
        Trace,
    };

    enum class InserterSpacing : std::uint8_t {
        Pad,
        AsIs,
    };

    static void add_output(ILogOutput::Ptr output);
    static void remove_all_outputs();
    static void set_formatter(ILogFormatter::Ptr new_formatter); // nullptr for the DefaultFormatter.
    static void set_level(Level level, std::string pattern = "");
    static void reset_levels(Level level);
    [[nodiscard]] static bool is_shown(Level level, std::string_view file = "");
    static void set_inserter_spacing(InserterSpacing new_spacing);

    explicit Logger(Level level, std::string_view file = {}, std::uint32_t line = 0,
                    std::string_view function = {}, bool condition_met = true,
                    std::string_view condition = {});

    ~Logger();

    [[nodiscard]] Level level() const noexcept;
    [[nodiscard]] std::string_view file() const noexcept;
    [[nodiscard]] std::uint32_t line() const noexcept;
    [[nodiscard]] std::string_view function() const noexcept;
    [[nodiscard]] std::string_view condition() const noexcept;

    template<typename Number, typename = std::enable_if_t<std::is_arithmetic_v<Number>>>
    Logger& operator<<(Number value);
    Logger& operator<<(std::string_view value);
    Logger& operator<<(const char* value);
    Logger& operator<<(const void* value);
    Logger& operator<<(double value);
    Logger& operator<<(const std::exception& value);

    Logger(const Logger&) = delete;
    Logger& operator=(const Logger&) = delete;
    Logger(Logger&&) = delete;
    Logger& operator=(Logger&&) = delete;

private:
    Level m_level;
    std::string_view m_file;
    std::uint32_t m_line;
    std::string_view m_function;
    bool m_condition_met;
    std::string_view m_condition;
    std::string m_text;

    Logger& _append(std::string_view value);
};

template<typename Number, typename>
Logger& Logger::operator<<(Number value) {
    if constexpr (std::is_floating_point_v<Number>) {
        return *this << static_cast<double>(value);
    } else if constexpr (std::is_signed_v<Number>) {
        return _append(std::to_string(static_cast<std::int64_t>(value)));
    } else {
        return _append(std::to_string(static_cast<std::uint64_t>(value)));
    }
}

} // namespace logger

// Each makes a Logger for a line at its level, from where it is written:
// lWarn << "Cannot reach " << address;. The ...If forms log only when their
// condition is true, and say it in the line. They are macros so that the
// file, line, and function are those of the line logged, which C++17 has no
// other way to give.
// NOLINTBEGIN(cppcoreguidelines-macro-usage)
#define LOGGER_AT(level) \
    ::logger::Logger(::logger::Logger::Level::level, __FILE__, __LINE__, __func__)
#define LOGGER_IF(level, condition)                                                \
    ::logger::Logger(::logger::Logger::Level::level, __FILE__, __LINE__, __func__, \
                     static_cast<bool>(condition), #condition)

#define lFatal LOGGER_AT(Fatal)
#define lFatalIf(condition) LOGGER_IF(Fatal, condition)
#define lLog LOGGER_AT(Log)
#define lErr LOGGER_AT(Error)
#define lErrIf(condition) LOGGER_IF(Error, condition)
#define lWarn LOGGER_AT(Warning)
#define lWarnIf(condition) LOGGER_IF(Warning, condition)
#define lInfo LOGGER_AT(Info)
#define lDebug LOGGER_AT(Debug)
#define lVerbose LOGGER_AT(Verbose)
#define lTrace LOGGER_AT(Trace)
// NOLINTEND(cppcoreguidelines-macro-usage)
