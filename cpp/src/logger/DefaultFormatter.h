#pragma once

#include <stdexcept>

#include "ILogFormatter.h"
#include "Logger.h"

#if defined(__GNUC__) || defined(__clang__)
#define STRFTIME_FORMAT(formatParameter) [[gnu::format(strftime, formatParameter, 0)]]
#else
#define STRFTIME_FORMAT(p)
#endif

namespace core {

class DefaultFormatter : public ILogFormatter {
public:
    enum Location { Gmt,
        Local };
    static std::string date(Location location);
    STRFTIME_FORMAT(2)
    static std::string date(Location location, const char *format);
    static std::string levelString(Logger::Level level);

    explicit DefaultFormatter(Location location = Local);
    ~DefaultFormatter() override = default;

    std::string format(const std::string &line, size_t thread, const Logger &logger) override;
    std::string format(const std::exception &exception) override;

private:
    Location _location;

    static std::string _ms_str(const std::chrono::system_clock::time_point &time);
    static std::string &_replace(std::string &str, const std::string &search, const std::string &replace);
};

}