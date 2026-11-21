#pragma once

#include <memory>
#include <string_view>

namespace logger {

class ILogOutput {
public:
    using Ptr = std::unique_ptr<ILogOutput>;

    virtual void log(std::string_view line) = 0;

    virtual ~ILogOutput() = default;
    ILogOutput(const ILogOutput&) = delete;
    ILogOutput& operator=(const ILogOutput&) = delete;
    ILogOutput(ILogOutput&&) noexcept = default;
    ILogOutput& operator=(ILogOutput&&) noexcept = default;

protected:
    ILogOutput() = default;
};

} // namespace logger
