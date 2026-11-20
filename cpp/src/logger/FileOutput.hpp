#pragma once

#include <string>

#include "StreamOutput.hpp"

namespace logger {

// Adds each line to the end of a file, which it makes if it is not there.
class FileOutput : public StreamOutput {
public:
    // Throws std::system_error when the file cannot be opened.
    explicit FileOutput(const std::string& path);
};

} // namespace logger
