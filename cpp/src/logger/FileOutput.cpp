#include "FileOutput.h"
#include <cstring>
#include <system_error>

namespace core {

FileOutput::FileOutput(const std::string &path)
    : StreamOutput(_open(path), path, StreamOutput::AutoClose) { }

FILE *FileOutput::_open(const std::string &path) {
    auto *opened = ::fopen(path.c_str(), "a");

    if (nullptr == opened) {
        const auto error = errno;

        throw std::system_error(
            std::error_code(error, std::generic_category()),
            "Failed to open log '" + path + "': " + ::strerror(error));
    }

    return opened;
}

}