#pragma once

#include <exception>
#include <string>

namespace core {

class Logger;

class ILogFormatter {
public:
    virtual ~ILogFormatter() = default;
    virtual std::string format(const std::string &message, size_t thread, const Logger &logger) = 0;
    virtual std::string format(const std::exception &exception) = 0;
};

}
