#include "Directories.hpp"

#include <filesystem>
#include <optional>
#include <string>

using std::getenv;
using std::nullopt;
using std::optional;
using std::string;
using std::filesystem::path;

namespace {
// The value of the environment variable name, or "" if it is not set.
string environment(const char* name) {
    // Read before the node starts a thread, and never set by the node.
    // NOLINTNEXTLINE(concurrency-mt-unsafe)
    const char* const value = getenv(name);

    return nullptr == value ? "" : value;
}

} // namespace

namespace io {

optional<path> Directories::user_app_data() {
    const auto home = environment("HOME");

#ifndef __APPLE__
    if (const auto data_home = environment("XDG_DATA_HOME"); !data_home.empty()) {
        return path(data_home);
    }
#endif

    if (home.empty()) {
        return nullopt;
    }

#ifdef __APPLE__
    return path(home) / "Library" / "Application Support";
#else
    return path(home) / ".local" / "share";
#endif
}

} // namespace io
