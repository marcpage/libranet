#pragma once

#include <string>

namespace core {

class ILogOutput {
public:
    virtual void log(const std::string &message) = 0;
    virtual ~ILogOutput() = default;
};

}