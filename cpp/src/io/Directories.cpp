#include "Directories.hpp"

#include <cstdlib>
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

// The directory at relative in HOME, if HOME is set.
optional<path> in_home(const path& relative) {
    const auto home = environment("HOME");

    if (home.empty()) {
        return nullopt;
    }

    return path(home) / relative;
}

#if __APPLE__
// Where macOS keeps each user's data and settings for applications.
optional<path> application_support() {
    return in_home(path("Library") / "Application Support");
}
#else
// The directory the XDG environment variable named variable gives, if it is
// set, or else the one at fallback in HOME, as the XDG Base Directory
// Specification has it.
optional<path> xdg_home(const char* variable, const path& fallback) {
    if (const auto directory = environment(variable); !directory.empty()) {
        return path(directory);
    }

    return in_home(fallback);
}
#endif

} // namespace

namespace io {

optional<path> Directories::user_app_data() {
#if __APPLE__
    return application_support();
#else
    return xdg_home("XDG_DATA_HOME", path(".local") / "share");
#endif
}

optional<path> Directories::user_config() {
#if __APPLE__
    return application_support();
#else
    return xdg_home("XDG_CONFIG_HOME", ".config");
#endif
}

} // namespace io
