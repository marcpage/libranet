#pragma once

#include "ILogOutput.h"
#include <syslog.h>

namespace core {

class SysLogOutput : public ILogOutput {
public:
    SysLogOutput() = default;
    ~SysLogOutput() override = default;

    void log(const std::string &message) override {
        ::syslog(LOG_NOTICE, "%s", message.c_str());
    }
};

}
