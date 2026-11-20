#pragma once

#include <cstdio>

#include "StreamOutput.hpp"

namespace logger {

class StdOutOutput : public StreamOutput {
public:
    StdOutOutput();
};

inline StdOutOutput::StdOutOutput()
    : StreamOutput(stdout, "stdout", Closing::LeaveOpen) {
}

} // namespace logger
