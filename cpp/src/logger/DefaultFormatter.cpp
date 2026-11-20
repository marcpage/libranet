#include "DefaultFormatter.hpp"

#include <chrono>
#include <cstddef>
#include <ctime>
#include <exception>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>

#include "Logger.hpp"

namespace {

// The calendar time of when, in GMT or local time.
std::tm calendar_time(std::time_t when, logger::DefaultFormatter::Location location) {
    std::tm time{};
    const auto* const converted = logger::DefaultFormatter::Location::Gmt == location
                                      ? ::gmtime_r(&when, &time)
                                      : ::localtime_r(&when, &time);

    if (nullptr == converted) {
        throw std::runtime_error("Cannot convert the time to a calendar time");
    }

    return time;
}

// The time now, as "2026-11-19 22:03:07.042 (Thu)" in GMT, or with its
// offset from GMT, as "2026-11-19 14:03:07.042 -0800 (Thu)", in local time.
std::string timestamp(logger::DefaultFormatter::Location location) {
    const auto now = std::chrono::system_clock::now();
    const auto time = calendar_time(std::chrono::system_clock::to_time_t(now), location);
    const auto milliseconds = std::chrono::duration_cast<std::chrono::milliseconds>(
        now.time_since_epoch() % std::chrono::seconds(1));
    std::ostringstream text;

    text << std::put_time(&time, "%Y-%m-%d %H:%M:%S") << '.' << std::setfill('0') << std::setw(3)
         << milliseconds.count();

    if (logger::DefaultFormatter::Location::Local == location) {
        text << std::put_time(&time, " %z");
    }

    text << std::put_time(&time, " (%a)");
    return text.str();
}

} // namespace

namespace logger {

std::string_view DefaultFormatter::level_string(Logger::Level level) {
    switch (level) {
        case Logger::Level::Fatal: return "FTL";
        case Logger::Level::Log: return "LOG";
        case Logger::Level::Error: return "ERR";
        case Logger::Level::Warning: return "WRN";
        case Logger::Level::Info: return "NFO";
        case Logger::Level::Debug: return "DBG";
        case Logger::Level::Verbose: return "VBS";
        case Logger::Level::Trace: return "TRC";
    }

    // A level cast from a number that names none of them.
    return "???";
}

DefaultFormatter::DefaultFormatter(Location location)
    : m_location(location) {
}

std::string DefaultFormatter::format(std::string_view message, std::size_t thread,
                                     const Logger& logger) const {
    std::ostringstream line;

    line << '[' << timestamp(m_location) << "][" << thread << "][" << level_string(logger.level())
         << ']';

    if (!logger.file().empty()) {
        line << '[' << logger.file() << ':' << logger.line() << ']';
    }

    if (!logger.function().empty()) {
        line << '[' << logger.function() << ']';
    }

    if (!logger.condition().empty()) {
        line << '[' << logger.condition() << ']';
    }

    line << ' ' << message << '\n';
    return line.str();
}

std::string DefaultFormatter::format(const std::exception& exception) const {
    return std::string("Exception: ") + exception.what();
}

} // namespace logger
