#include "FileOutput.hpp"

#include <cerrno>
#include <cstdio>
#include <filesystem>
#include <system_error>

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include "StreamOutput.hpp"

using std::FILE;
using std::system_error;
using std::filesystem::path;

namespace {

// Opens the file to add to its end. A file it makes can be read and written
// by its owner only, since a log's lines can name peers and what they asked
// for. A program this one starts does not inherit it.
FILE* open_to_append(const path& file) {
    constexpr int flags = O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC;
    constexpr mode_t owner_read_write = S_IRUSR | S_IWUSR;

    // open() is the POSIX call that sets the mode of a file as it makes it,
    // and it takes the mode as C's variable arguments.
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg)
    const int descriptor = ::open(file.c_str(), flags, owner_read_write);

    if (descriptor < 0) {
        throw system_error(errno, std::generic_category(),
                           "Cannot open the log file " + file.string());
    }

    // StreamOutput holds the stream in a std::unique_ptr, which closes it,
    // and the descriptor with it.
    auto* const stream = ::fdopen(descriptor, "a");

    if (nullptr == stream) {
        const auto error = errno;

        (void)::close(descriptor);
        throw system_error(error, std::generic_category(),
                           "Cannot open the log file " + file.string());
    }

    return stream;
}

} // namespace

namespace logger {

FileOutput::FileOutput(const path& file)
    : StreamOutput(open_to_append(file), file.string(), Closing::Close) {
}

} // namespace logger
