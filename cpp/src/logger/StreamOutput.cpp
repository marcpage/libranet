#include "StreamOutput.h"
#include <cstring>
#include <system_error>

namespace core {

StreamOutput::StreamOutput(FILE *stream, std::string name, CloseAction action)
    : _name(std::move(name))
    , _stream(stream)
    , _close(action == AutoClose) { }

StreamOutput::~StreamOutput() {
    if (_close && nullptr != _stream) {
        if (::fclose(_stream) != 0) {
            // too late
        }
    }
}

void StreamOutput::log(const std::string &line) {
    errno = 0;
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-const-cast)
    const auto amount = ::fwrite(const_cast<const char *>(line.data()), 1, line.size(), _stream);
    const auto error = errno;

    if (::ferror(_stream) != 0) {
        throw std::system_error(
            std::error_code(error, std::generic_category()),
            "Failed to log to '" + _name + "': " + ::strerror(error));
    }

    if (amount < line.size()) {
        throw std::runtime_error("Incomplete write to " + _name);
    }

    const auto results = ::fflush(_stream);

    if (results != 0) {
        throw std::system_error(
            std::error_code(error, std::generic_category()),
            "Failed to flush to '" + _name + "': " + ::strerror(error));
    }
}

}