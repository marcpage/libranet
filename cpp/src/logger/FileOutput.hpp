#pragma once

#include <filesystem>

#include "StreamOutput.hpp"

namespace logger {

class FileOutput : public StreamOutput {
public:
    // Throws std::system_error when the file cannot be opened.
    explicit FileOutput(const std::filesystem::path& file);
};

} // namespace logger
