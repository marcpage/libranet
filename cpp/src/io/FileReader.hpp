#pragma once

#include <cstddef>
#include <filesystem>
#include <fstream>
#include <optional>
#include <string>

#include "IReader.hpp"

namespace io {

// Reads a file it opens, from its start, and closes it when it goes.
class FileReader final : public IReader {
public:
    // Throws std::runtime_error when file cannot be opened, or is a
    // directory, which some systems open as a file that reads as empty.
    explicit FileReader(std::filesystem::path file);

    // All that is left of the file. Throws std::runtime_error when it cannot
    // be read.
    [[nodiscard]] std::string read_all();

    // Throws std::runtime_error when the file cannot be read.
    [[nodiscard]] std::optional<std::size_t> read_some(std::byte* data, std::size_t size) override;

private:
    std::filesystem::path m_file;
    std::ifstream m_stream;
};

} // namespace io
