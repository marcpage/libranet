#include <gtest/gtest.h>
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <ios>
#include <iterator>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

#include "io/SafeWriteFile.hpp"

using io::SafeWriteFile;

namespace {

// A directory of the running test's own, made under the system's directory
// for temporary files, and removed with all it holds when this is destroyed.
// Its name holds the test's and a random number, so that two runs of the
// tests at once do not share one.
class ScratchDirectory {
public:
    ScratchDirectory() {
        const auto* const test = testing::UnitTest::GetInstance()->current_test_info();
        std::random_device random;

        m_path = std::filesystem::temp_directory_path()
                 / (std::string("libranet-") + test->test_suite_name() + "-" + test->name() + "-"
                    + std::to_string(random()));
        std::filesystem::create_directories(m_path);
    }

    ~ScratchDirectory() {
        std::error_code error;

        std::filesystem::remove_all(m_path, error);
    }

    ScratchDirectory(const ScratchDirectory&) = delete;
    ScratchDirectory& operator=(const ScratchDirectory&) = delete;
    ScratchDirectory(ScratchDirectory&&) = delete;
    ScratchDirectory& operator=(ScratchDirectory&&) = delete;

    [[nodiscard]] const std::filesystem::path& path() const noexcept {
        return m_path;
    }

private:
    std::filesystem::path m_path;
};

// Makes directory the current one while this is, and the one that was
// current again after.
class InDirectory {
public:
    explicit InDirectory(const std::filesystem::path& directory)
        : m_previous(std::filesystem::current_path()) {
        std::filesystem::current_path(directory);
    }

    ~InDirectory() {
        std::error_code error;

        std::filesystem::current_path(m_previous, error);
    }

    InDirectory(const InDirectory&) = delete;
    InDirectory& operator=(const InDirectory&) = delete;
    InDirectory(InDirectory&&) = delete;
    InDirectory& operator=(InDirectory&&) = delete;

private:
    std::filesystem::path m_previous;
};

// The file at path, with text in it.
void write_file(const std::filesystem::path& path, std::string_view text) {
    std::ofstream file(path, std::ios::binary | std::ios::trunc);

    file << text;
}

// The text in the file at path.
std::string read_file(const std::filesystem::path& path) {
    const std::ifstream file(path, std::ios::binary);
    std::ostringstream text;

    text << file.rdbuf();
    return text.str();
}

// The names of what is in directory, in order.
std::vector<std::string> names_in(const std::filesystem::path& directory) {
    std::vector<std::string> names;

    std::transform(std::filesystem::directory_iterator(directory),
                   std::filesystem::directory_iterator(), std::back_inserter(names),
                   [](const std::filesystem::directory_entry& entry) {
        return entry.path().filename().string();
    });
    std::sort(names.begin(), names.end());
    return names;
}

} // namespace

// Once committed, nothing is left but the file.
TEST(SafeWriteFileTest, CommitRenamesWhatWasWrittenIntoPlace) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";

    {
        SafeWriteFile file(path);

        write_file(file.writable_path(), "hello");
        file.commit();
    }

    EXPECT_EQ(names_in(directory.path()), std::vector<std::string>{"file"});
    EXPECT_EQ(read_file(path), "hello");
}

// Until it is committed, the file is not there, and what is written is
// beside where it goes.
TEST(SafeWriteFileTest, WritablePathIsAPartialFileBesideTheFile) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";
    const SafeWriteFile file(path);

    write_file(file.writable_path(), "hello");

    EXPECT_NE(file.writable_path(), path);
    EXPECT_EQ(file.writable_path().parent_path(), directory.path());
    EXPECT_TRUE(SafeWriteFile::is_partial(file.writable_path()));
    EXPECT_FALSE(std::filesystem::exists(path));
}

TEST(SafeWriteFileTest, TwoWritesOfOneFileWriteToDifferentPaths) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";
    const SafeWriteFile first(path);
    const SafeWriteFile second(path);

    EXPECT_NE(first.writable_path(), second.writable_path());
}

TEST(SafeWriteFileTest, ConstructorMakesTheDirectoriesTheFileIsIn) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "missing" / "nested" / "file";

    const SafeWriteFile file(path);

    EXPECT_TRUE(std::filesystem::is_directory(path.parent_path()));
}

// A regular file where a directory should be: none can be made in it.
TEST(SafeWriteFileTest, ConstructorWhereNoDirectoryCanBeMadeIsRefused) {
    const ScratchDirectory directory;
    const auto not_a_directory = directory.path() / "file";

    write_file(not_a_directory, "hello");

    // Mistaken: it is made only to see that making it throws.
    // cppcheck-suppress unusedScopedObject
    EXPECT_THROW(SafeWriteFile(not_a_directory / "file"), std::runtime_error);
}

TEST(SafeWriteFileTest, FileWithNoDirectoryIsInTheCurrentOne) {
    const ScratchDirectory directory;
    const InDirectory current(directory.path());

    {
        SafeWriteFile file("file");

        write_file(file.writable_path(), "hello");
        file.commit();
    }

    EXPECT_EQ(names_in(directory.path()), std::vector<std::string>{"file"});
    EXPECT_EQ(read_file(directory.path() / "file"), "hello");
}

// As when what writes it throws.
TEST(SafeWriteFileTest, WhatIsNotCommittedIsRemoved) {
    const ScratchDirectory directory;

    {
        const SafeWriteFile file(directory.path() / "file");

        write_file(file.writable_path(), "hello");
    }

    EXPECT_TRUE(names_in(directory.path()).empty());
}

TEST(SafeWriteFileTest, NothingWrittenAndNotCommittedLeavesNothing) {
    const ScratchDirectory directory;

    {
        const SafeWriteFile file(directory.path() / "file");
    }

    EXPECT_TRUE(names_in(directory.path()).empty());
}

TEST(SafeWriteFileTest, CommitReplacesTheFileThereBefore) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";

    write_file(path, "before");

    {
        SafeWriteFile file(path);

        write_file(file.writable_path(), "after");
        file.commit();
    }

    EXPECT_EQ(names_in(directory.path()), std::vector<std::string>{"file"});
    EXPECT_EQ(read_file(path), "after");
}

// What was there before is left as it was.
TEST(SafeWriteFileTest, WhatIsNotCommittedLeavesTheFileThereBefore) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";

    write_file(path, "before");

    {
        const SafeWriteFile file(path);

        write_file(file.writable_path(), "after");
    }

    EXPECT_EQ(names_in(directory.path()), std::vector<std::string>{"file"});
    EXPECT_EQ(read_file(path), "before");
}

TEST(SafeWriteFileTest, CommitWithNothingWrittenIsRefused) {
    const ScratchDirectory directory;
    SafeWriteFile file(directory.path() / "file");

    EXPECT_THROW(file.commit(), std::runtime_error);
}

// A directory where the file goes: what is written beside it cannot be
// renamed into its place, and is removed.
TEST(SafeWriteFileTest, CommitOverADirectoryIsRefusedAndLeavesNothingWritten) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";

    std::filesystem::create_directories(path);

    {
        SafeWriteFile file(path);

        write_file(file.writable_path(), "hello");

        EXPECT_THROW(file.commit(), std::runtime_error);
    }

    EXPECT_EQ(names_in(directory.path()), std::vector<std::string>{"file"});
    EXPECT_TRUE(std::filesystem::is_directory(path));
}

TEST(SafeWriteFileTest, CommittingTwiceIsRefused) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";
    SafeWriteFile file(path);

    write_file(file.writable_path(), "hello");
    file.commit();

    EXPECT_THROW(file.commit(), std::logic_error);
    EXPECT_EQ(read_file(path), "hello");
}

TEST(SafeWriteFileTest, WriteCommitsWhatItWrites) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";
    SafeWriteFile file(path);

    file.write("hello");

    EXPECT_EQ(names_in(directory.path()), std::vector<std::string>{"file"});
    EXPECT_EQ(read_file(path), "hello");
}

// The directory the file is in was removed after the file was made.
TEST(SafeWriteFileTest, WriteWhereTheDirectoryIsGoneIsRefused) {
    const ScratchDirectory directory;
    const auto gone = directory.path() / "gone";
    SafeWriteFile file(gone / "file");

    std::filesystem::remove_all(gone);

    EXPECT_THROW(file.write("hello"), std::runtime_error);
    EXPECT_FALSE(std::filesystem::exists(gone));
}

// Only a name that starts with "." and ends with ".partial" is one, wherever
// it is.
TEST(SafeWriteFileTest, IsPartialTellsAPartialFileByItsName) {
    EXPECT_TRUE(SafeWriteFile::is_partial(".file.1.partial"));
    EXPECT_TRUE(SafeWriteFile::is_partial(std::filesystem::path("directory") / ".file.1.partial"));
    EXPECT_FALSE(SafeWriteFile::is_partial("file"));
    EXPECT_FALSE(SafeWriteFile::is_partial(".file"));
    EXPECT_FALSE(SafeWriteFile::is_partial("file.partial"));
    EXPECT_FALSE(SafeWriteFile::is_partial(".partial"));
    EXPECT_FALSE(SafeWriteFile::is_partial(".file.1.partial.old"));
    EXPECT_FALSE(SafeWriteFile::is_partial(std::filesystem::path(".file.1.partial") / "file"));
}
