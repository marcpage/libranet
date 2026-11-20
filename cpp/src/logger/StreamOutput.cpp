#include "StreamOutput.hpp"

#include <cerrno>
#include <cstdio>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>

using std::FILE;
using std::string;
using std::string_view;
using std::system_error;

namespace logger {

StreamOutput::StreamOutput(FILE* stream, string name, Closing closing)
    : m_name(std::move(name))
    , m_stream(stream, Closer(closing)) {
}

void StreamOutput::log(string_view line) {
    // fwrite() writes less than it is given only when it fails, and both it
    // and fflush() set errno when they do.
    if (std::fwrite(line.data(), 1, line.size(), m_stream.get()) < line.size()
        || 0 != std::fflush(m_stream.get())) {
        throw system_error(errno, std::generic_category(),
                           "Cannot write the log to " + m_name);
    }
}

StreamOutput::Closer::Closer(Closing closing) noexcept
    : m_closing(closing) {
}

void StreamOutput::Closer::operator()(FILE* stream) const noexcept {
    // A stream that cannot be closed has nothing left to report it to. This
    // is the deleter of the std::unique_ptr that owns the stream.
    if (Closing::Close == m_closing) {
        // NOLINTNEXTLINE(cppcoreguidelines-owning-memory)
        (void)std::fclose(stream);
    }
}

} // namespace logger
