#pragma once

#include <gtest/gtest.h>
#include <filesystem>
#include <random>
#include <string>
#include <system_error>

// What more than one of the db tests uses: a directory of a test's own to
// keep a database in.
namespace fixtures {

// A directory of the running test's own, made under the system's directory
// for temporary files, and removed with all it holds when this is destroyed.
// SQLite writes files beside the database, which go with it.
class ScratchDirectory {
public:
    ScratchDirectory();
    ~ScratchDirectory();

    // Where the database is kept in this directory.
    [[nodiscard]] std::filesystem::path database() const;

    ScratchDirectory(const ScratchDirectory&) = delete;
    ScratchDirectory& operator=(const ScratchDirectory&) = delete;
    ScratchDirectory(ScratchDirectory&&) = delete;
    ScratchDirectory& operator=(ScratchDirectory&&) = delete;

private:
    std::filesystem::path m_path;
};

inline ScratchDirectory::ScratchDirectory() {
    const auto* const test = testing::UnitTest::GetInstance()->current_test_info();
    std::random_device random;

    m_path = std::filesystem::temp_directory_path()
             / (std::string("libranet-") + test->test_suite_name() + "-" + test->name() + "-"
                + std::to_string(random()));
    std::filesystem::create_directories(m_path);
}

inline ScratchDirectory::~ScratchDirectory() {
    std::error_code error;

    std::filesystem::remove_all(m_path, error);
}

inline std::filesystem::path ScratchDirectory::database() const {
    return m_path / "libranet.sqlite3";
}

} // namespace fixtures
