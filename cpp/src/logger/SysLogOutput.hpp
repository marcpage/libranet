#pragma once

#include <string_view>

#include "ILogOutput.hpp"

namespace logger {

// Sends each line to the system log, at the notice level.
class SysLogOutput : public ILogOutput {
public:
    void log(std::string_view line) override;
};

} // namespace logger
