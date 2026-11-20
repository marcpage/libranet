#pragma once

#include <cstddef>
#include <exception>
#include <memory>
#include <string>
#include <string_view>

namespace logger {

class Logger;

class ILogFormatter {
public:
    using Ptr = std::unique_ptr<ILogFormatter>;

    [[nodiscard]] virtual std::string format(std::string_view message, std::size_t thread,
                                             const Logger& logger) const = 0;
    [[nodiscard]] virtual std::string format(const std::exception& error) const = 0;

    virtual ~ILogFormatter() = default;
    ILogFormatter(const ILogFormatter&) = delete;
    ILogFormatter& operator=(const ILogFormatter&) = delete;
    ILogFormatter(ILogFormatter&&) noexcept = default;
    ILogFormatter& operator=(ILogFormatter&&) noexcept = default;

protected:
    ILogFormatter() = default;
};

} // namespace logger
