#include "SysLogOutput.hpp"

#include <string>
#include <string_view>

#include <syslog.h>

namespace logger {

void SysLogOutput::log(std::string_view line) {
    const std::string text(line);

    // syslog() takes C's variable arguments, as printf() does.
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg)
    ::syslog(LOG_NOTICE, "%s", text.c_str());
}

} // namespace logger
