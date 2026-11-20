#pragma once

#include <string>

#include "StreamOutput.hpp"

namespace logger {

class FileOutput : public StreamOutput {
public:
    // Throws std::system_error when the file cannot be opened.
    explicit FileOutput(const std::string& path);
};

} // namespace logger
