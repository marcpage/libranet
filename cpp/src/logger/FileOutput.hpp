#pragma once

#include <string>

#include "StreamOutput.hpp"

namespace logger {

// Adds each line to the end of a file. If the file is not there, it makes
// one that only its owner can read and write.
class FileOutput : public StreamOutput {
public:
    // Throws std::system_error when the file cannot be opened.
    explicit FileOutput(const std::string& path);
};

} // namespace logger
