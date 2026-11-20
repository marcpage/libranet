#include "FileOutput.hpp"

#include <cerrno>
#include <cstdio>
#include <string>
#include <system_error>

#include "StreamOutput.hpp"

namespace {

// Opens the file to add to its end, making it if it is not there. The "e"
// keeps a program this one starts from inheriting it, on Linux; macOS
// takes the letter and does not act on it.
std::FILE* open_to_append(const std::string& path) {
    // StreamOutput holds the stream in a std::unique_ptr, which closes it.
    // NOLINTNEXTLINE(cppcoreguidelines-owning-memory)
    auto* const stream = std::fopen(path.c_str(), "ae");

    if (nullptr == stream) {
        throw std::system_error(errno, std::generic_category(), "Cannot open the log file " + path);
    }

    return stream;
}

} // namespace

namespace logger {

FileOutput::FileOutput(const std::string& path)
    : StreamOutput(open_to_append(path), path, Closing::Close) {
}

} // namespace logger
