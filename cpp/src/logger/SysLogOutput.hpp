#pragma once

#include <string_view>

#include "ILogOutput.hpp"

namespace logger {

class SysLogOutput : public ILogOutput {
public:
    void log(std::string_view line) override;
};

} // namespace logger
