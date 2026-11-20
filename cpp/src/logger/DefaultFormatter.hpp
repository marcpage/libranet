#pragma once

#include <cstddef>
#include <cstdint>
#include <exception>
#include <string>
#include <string_view>

#include "ILogFormatter.hpp"
#include "Logger.hpp"

namespace logger {

// Writes a line as
// "[2026-11-19 14:03:07.042 -0800 (Thu)][0][WRN][src/net/Socket.cpp:88][send] text",
// with the time to the millisecond, the index of the thread, the level, where
// the line was logged, and the condition it was logged on, if any. In GMT the
// time has no offset.
class DefaultFormatter : public ILogFormatter {
public:
    // Which time a line is stamped with.
    enum class Location : std::uint8_t {
        Gmt,
        Local,
    };

    // Three letters for the level, as "WRN" for Logger::Level::Warning.
    [[nodiscard]] static std::string_view level_string(Logger::Level level);

    explicit DefaultFormatter(Location location = Location::Local);

    [[nodiscard]] std::string format(std::string_view message, std::size_t thread,
                                     const Logger& logger) const override;
    // "Exception: " and what the exception says.
    [[nodiscard]] std::string format(const std::exception& exception) const override;

private:
    Location m_location;
};

} // namespace logger
