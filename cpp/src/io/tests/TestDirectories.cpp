#include <gtest/gtest.h>
#include <cstdlib>
#include <filesystem>
#include <optional>
#include <string>
#include <utility>

#include "io/Directories.hpp"

using io::Directories;
using std::nullopt;
using std::optional;
using std::string;
using std::filesystem::path;

namespace {

constexpr auto home = "/home/someone";

// Sets the environment variable name to value, or unsets it for none, until
// this is destroyed, when it is as it was.
class ScopedVariable {
public:
    ScopedVariable(string name, const optional<string>& value);
    ~ScopedVariable();

    ScopedVariable(const ScopedVariable&) = delete;
    ScopedVariable& operator=(const ScopedVariable&) = delete;
    ScopedVariable(ScopedVariable&&) = delete;
    ScopedVariable& operator=(ScopedVariable&&) = delete;

private:
    string m_name;
    optional<string> m_previous;

    void _set(const optional<string>& value) const;
};

ScopedVariable::ScopedVariable(string name, const optional<string>& value)
    : m_name(std::move(name)) {
    // NOLINTNEXTLINE(concurrency-mt-unsafe)
    if (const char* const previous = std::getenv(m_name.c_str()); nullptr != previous) {
        m_previous = previous;
    }

    _set(value);
}

ScopedVariable::~ScopedVariable() {
    _set(m_previous);
}

void ScopedVariable::_set(const optional<string>& value) const {
    if (value.has_value()) {
        // NOLINTNEXTLINE(concurrency-mt-unsafe)
        (void)::setenv(m_name.c_str(), value->c_str(), 1);
    } else {
        // NOLINTNEXTLINE(concurrency-mt-unsafe)
        (void)::unsetenv(m_name.c_str());
    }
}

} // namespace

TEST(DirectoriesTest, UserAppDataInHome) {
    const ScopedVariable given_home("HOME", home);
    const ScopedVariable data_home("XDG_DATA_HOME", nullopt);

#if __APPLE__
    EXPECT_EQ(Directories::user_app_data(), path(home) / "Library" / "Application Support");
#else
    EXPECT_EQ(Directories::user_app_data(), path(home) / ".local" / "share");
#endif
}

TEST(DirectoriesTest, NoUserAppDataWithoutHome) {
    const ScopedVariable given_home("HOME", nullopt);
    const ScopedVariable data_home("XDG_DATA_HOME", nullopt);

    EXPECT_EQ(Directories::user_app_data(), nullopt);
}

TEST(DirectoriesTest, UserConfigInHome) {
    const ScopedVariable given_home("HOME", home);
    const ScopedVariable config_home("XDG_CONFIG_HOME", nullopt);

#if __APPLE__
    EXPECT_EQ(Directories::user_config(), path(home) / "Library" / "Application Support");
#else
    EXPECT_EQ(Directories::user_config(), path(home) / ".config");
#endif
}

TEST(DirectoriesTest, NoUserConfigWithoutHome) {
    const ScopedVariable given_home("HOME", nullopt);
    const ScopedVariable config_home("XDG_CONFIG_HOME", nullopt);

    EXPECT_EQ(Directories::user_config(), nullopt);
}

// An empty variable is as one not set.
TEST(DirectoriesTest, UserConfigWithEmptyXdgConfigHome) {
    const ScopedVariable given_home("HOME", home);
    const ScopedVariable config_home("XDG_CONFIG_HOME", "");

#if __APPLE__
    EXPECT_EQ(Directories::user_config(), path(home) / "Library" / "Application Support");
#else
    EXPECT_EQ(Directories::user_config(), path(home) / ".config");
#endif
}

#if __APPLE__
// macOS keeps them in Application Support, whatever the XDG variables say.
TEST(DirectoriesTest, XdgVariablesNotUsedOnMacos) {
    const ScopedVariable given_home("HOME", home);
    const ScopedVariable data_home("XDG_DATA_HOME", "/elsewhere/data");
    const ScopedVariable config_home("XDG_CONFIG_HOME", "/elsewhere/config");

    EXPECT_EQ(Directories::user_app_data(), path(home) / "Library" / "Application Support");
    EXPECT_EQ(Directories::user_config(), path(home) / "Library" / "Application Support");
}
#else
TEST(DirectoriesTest, UserAppDataInXdgDataHome) {
    const ScopedVariable given_home("HOME", nullopt);
    const ScopedVariable data_home("XDG_DATA_HOME", "/elsewhere/data");

    EXPECT_EQ(Directories::user_app_data(), path("/elsewhere/data"));
}

TEST(DirectoriesTest, UserConfigInXdgConfigHome) {
    const ScopedVariable given_home("HOME", nullopt);
    const ScopedVariable config_home("XDG_CONFIG_HOME", "/elsewhere/config");

    EXPECT_EQ(Directories::user_config(), path("/elsewhere/config"));
}

// Each directory is found from its own variable.
TEST(DirectoriesTest, XdgVariablesKeptApart) {
    const ScopedVariable given_home("HOME", home);
    const ScopedVariable data_home("XDG_DATA_HOME", "/elsewhere/data");
    const ScopedVariable config_home("XDG_CONFIG_HOME", nullopt);

    EXPECT_EQ(Directories::user_config(), path(home) / ".config");
}
#endif
