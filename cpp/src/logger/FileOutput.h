#pragma once

#include "StreamOutput.h"

namespace core {

class FileOutput : public StreamOutput {
public:
    explicit FileOutput(const std::string &path);
    FileOutput(const FileOutput &) = delete;
    FileOutput &operator=(const FileOutput &) = delete;
    ~FileOutput() override = default;

private:
    static FILE *_open(const std::string &path);
};

}
