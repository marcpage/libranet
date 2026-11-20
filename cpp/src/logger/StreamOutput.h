#pragma once

#include "ILogOutput.h"
#include <stdio.h>
#include <string>

namespace core {

class StreamOutput : public ILogOutput {
public:
    enum CloseAction { AutoClose,
        DoNotClose };
    StreamOutput(FILE *stream, std::string name, CloseAction action = AutoClose);
    StreamOutput(const StreamOutput &) = delete;
    StreamOutput &operator=(const StreamOutput &) = delete;
    ~StreamOutput() override;

    void log(const std::string &line) override;

protected:
    std::string _name;

private:
    FILE *_stream;
    bool _close;
};

}
