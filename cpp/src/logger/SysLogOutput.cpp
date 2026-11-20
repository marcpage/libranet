#include "SysLogOutput.hpp"

#include <string>
#include <string_view>

#include <syslog.h>

using std::string;
using std::string_view;

namespace logger {

void SysLogOutput::log(string_view line) {
    const string text(line);

    // syslog() takes C's variable arguments, as printf() does.
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg)
    ::syslog(LOG_NOTICE, "%s", text.c_str());
}

} // namespace logger
