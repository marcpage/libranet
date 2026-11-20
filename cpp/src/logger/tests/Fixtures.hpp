#pragma once

#include <gtest/gtest.h>
#include <filesystem>
#include <fstream>
#include <ios>
#include <random>
#include <sstream>
#include <string>
#include <system_error>

// What more than one of the logger tests uses: a file to write a log to, and
// a way to read it back.
namespace fixtures {

// A path in the system's temporary directory, named for the test, whose file
// is removed when the test ends.
class ScratchFile {
public:
    ScratchFile();
    ~ScratchFile();
    ScratchFile(const ScratchFile&) = delete;
    ScratchFile& operator=(const ScratchFile&) = delete;
    ScratchFile(ScratchFile&&) = delete;
    ScratchFile& operator=(ScratchFile&&) = delete;

    [[nodiscard]] const std::filesystem::path& path() const noexcept;

private:
    std::filesystem::path m_path;
};

inline ScratchFile::ScratchFile() {
    const auto* const test = testing::UnitTest::GetInstance()->current_test_info();
    std::random_device random;

    m_path = std::filesystem::temp_directory_path()
             / (std::string("libranet-") + test->test_suite_name() + "-" + test->name() + "-"
                + std::to_string(random()) + ".log");
}

inline ScratchFile::~ScratchFile() {
    std::error_code error;

    std::filesystem::remove(m_path, error);
}

inline const std::filesystem::path& ScratchFile::path() const noexcept {
    return m_path;
}

// What the file at path holds.
inline std::string read_file(const std::filesystem::path& path) {
    const std::ifstream in_stream(path, std::ios::binary);
    std::ostringstream text;

    text << in_stream.rdbuf();
    return text.str();
}

} // namespace fixtures
