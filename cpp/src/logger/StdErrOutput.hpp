#pragma once

#include <cstdio>

#include "StreamOutput.hpp"

namespace logger {

class StdErrOutput : public StreamOutput {
public:
    StdErrOutput();
};

inline StdErrOutput::StdErrOutput()
    : StreamOutput(stderr, "stderr", Closing::LeaveOpen) {
}

} // namespace logger
