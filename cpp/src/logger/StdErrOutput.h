#pragma once

#include "StreamOutput.h"

namespace core {

class StdErrOutput : public StreamOutput {
public:
    StdErrOutput()
        : StreamOutput(stderr, "stderr", StreamOutput::DoNotClose) { }
    StdErrOutput(const StdErrOutput &) = delete;
    StdErrOutput &operator=(const StdErrOutput &) = delete;
    ~StdErrOutput() override = default;
};

}
