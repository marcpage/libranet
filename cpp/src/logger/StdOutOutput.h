#pragma once

#include "StreamOutput.h"

namespace core {

class StdOutOutput : public StreamOutput {
public:
    StdOutOutput()
        : StreamOutput(stdout, "stdout", StreamOutput::DoNotClose) { }
    StdOutOutput(const StdOutOutput &) = delete;
    StdOutOutput &operator=(const StdOutOutput &) = delete;
    ~StdOutOutput() override = default;
};

}
