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

using logger::DefaultFormatter;
using std::exception;
using std::ostringstream;
using std::runtime_error;
using std::size_t;
using std::string;
using std::string_view;
using std::time_t;
using std::tm;
using std::chrono::milliseconds;
using std::chrono::seconds;
using std::chrono::system_clock;

namespace {

// The calendar time of when, in GMT or local time.
tm calendar_time(time_t when, DefaultFormatter::Location location) {
    tm time{};
    const auto* const converted = DefaultFormatter::Location::Gmt == location
                                      ? ::gmtime_r(&when, &time)
                                      : ::localtime_r(&when, &time);

    if (nullptr == converted) {
        throw runtime_error("Cannot convert the time to a calendar time");
    }

    return time;
}

// The time now, as "2026-11-19 22:03:07.042 (Thu)" in GMT, or with its
// offset from GMT, as "2026-11-19 14:03:07.042 -0800 (Thu)", in local time.
string timestamp(DefaultFormatter::Location location) {
    const auto now = system_clock::now();
    const auto time = calendar_time(system_clock::to_time_t(now), location);
    const auto past_second =
        std::chrono::duration_cast<milliseconds>(now.time_since_epoch() % seconds(1));
    ostringstream text;

    text << std::put_time(&time, "%Y-%m-%d %H:%M:%S") << '.' << std::setfill('0') << std::setw(3)
         << past_second.count();

    if (DefaultFormatter::Location::Local == location) {
        text << std::put_time(&time, " %z");
    }

    text << std::put_time(&time, " (%a)");
    return text.str();
}

} // namespace

namespace logger {

string_view DefaultFormatter::level_string(Logger::Level level) {
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

string DefaultFormatter::format(string_view message, size_t thread,
                                const Logger& logger) const {
    ostringstream line;

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

string DefaultFormatter::format(const exception& error) const {
    return string("Exception: ") + error.what();
}

} // namespace logger
