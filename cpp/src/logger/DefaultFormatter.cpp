#include "DefaultFormatter.h"
#include <cstring>
#include <exception>

namespace core {

DefaultFormatter::DefaultFormatter(Location location)
    : _location(location) { }

std::string DefaultFormatter::format(const std::string &line, size_t thread, const Logger &logger) {
    return "[" + date(_location)
        + "][" + std::to_string(thread)
        + "][" + levelString(logger.levelRequested)
        + (nullptr != logger.file ? ("][" + std::string(logger.file) + ":" + std::to_string(logger.line)) : std::string())
        + (nullptr != logger.function ? ("][" + std::string(logger.function)) : std::string())
        + (nullptr != logger.condition ? ("][" + std::string(logger.condition)) : std::string())
        + "] " + line + "\n";
}

std::string DefaultFormatter::format(const std::exception &exception) {
    return std::string("Exception: ") + exception.what();
}

std::string DefaultFormatter::date(Location location) {
    constexpr const char *GMT_FORMAT = "%Y-%m-%d %H:%M:%S.MS (%a)";
    constexpr const char *LOCAL_FORMAT = "%Y-%m-%d %H:%M:%S.MS %z (%a)";

    return date(location, Gmt == location ? GMT_FORMAT : LOCAL_FORMAT);
}

STRFTIME_FORMAT(3)
std::string DefaultFormatter::date(Location location, const char *format) {
    const auto nowHiRes = std::chrono::system_clock::now();
    const auto nowSeconds = std::chrono::system_clock::to_time_t(nowHiRes);
    const auto milliseconds = _ms_str(nowHiRes);
    std::string buffer;
    const auto maxTimeStringSize = 35;

    struct tm now; // NOLINT(cppcoreguidelines-pro-type-member-init)

    ::memset(&now, 0, sizeof(now));

    auto * const result = Gmt == location
        ? ::gmtime_r(&nowSeconds, &now)
        : ::localtime_r(&nowSeconds, &now);

    if (nullptr == result) {
        throw std::runtime_error("Unable to get time");
    }

    buffer.assign(maxTimeStringSize, '\0');

#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wformat-nonliteral"
    const std::size_t size = ::strftime(buffer.data(), buffer.size(), format, &now);
#pragma GCC diagnostic pop

    if (0 == size) {
        throw std::runtime_error("Unable to format time");
    }

    buffer.erase(size);
    return _replace(buffer, ".MS", "." + milliseconds);
}

std::string DefaultFormatter::levelString(Logger::Level level) {
    switch (level) {
    case Logger::Level::Fatal:
        return "FTL";
    case Logger::Level::Log:
        return "LOG";
    case Logger::Level::Error:
        return "ERR";
    case Logger::Level::Warning:
        return "WRN";
    case Logger::Level::Info:
        return "NFO";
    case Logger::Level::Debug:
        return "DBG";
    case Logger::Level::Verbose:
        return "VBS";
    case Logger::Level::Trace:
        return "TRC";
    default:
        return "???";
    }
}

std::string DefaultFormatter::_ms_str(const std::chrono::system_clock::time_point &time) {
    const auto now = time.time_since_epoch();
    const auto now_in_ms = std::chrono::duration_cast<std::chrono::milliseconds>(now);
    const auto msInSec = 1000;
    auto ms_str = std::to_string((now_in_ms % msInSec).count());

    while (ms_str.size() < 3) {
        ms_str.insert(0, 1, '0');
    }

    return ms_str;
}

std::string &DefaultFormatter::_replace(std::string &str, const std::string &search, const std::string &replace) {
    size_t pos = 0;

    while ((pos = str.find(search, pos)) != std::string::npos) {
        str.replace(pos, search.length(), replace);
        pos += replace.length();
    }

    return str;
}

}