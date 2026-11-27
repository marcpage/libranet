#include <gtest/gtest.h>
#include <algorithm>
#include <cstddef>
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

// What only its owner may do with a file made for Access::Owner.
constexpr auto owner_only = std::filesystem::perms::owner_read
                            | std::filesystem::perms::owner_write;

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

    [[nodiscard]] const std::filesystem::path& path() const noexcept {
        return m_path;
    }

    ScratchDirectory(const ScratchDirectory&) = delete;
    ScratchDirectory& operator=(const ScratchDirectory&) = delete;
    ScratchDirectory(ScratchDirectory&&) = delete;
    ScratchDirectory& operator=(ScratchDirectory&&) = delete;

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

// Bytes are written as they are, and no bytes at all make an empty file.
TEST(SafeWriteFileTest, WriteCommitsTheBytesItWrites) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";
    const auto empty_path = directory.path() / "empty";
    const std::vector<std::byte> bytes{std::byte{0x00}, std::byte{0x0A}, std::byte{0xFF}};
    SafeWriteFile file(path);
    SafeWriteFile empty(empty_path);

    file.write(bytes);
    empty.write(std::vector<std::byte>());

    EXPECT_EQ(names_in(directory.path()), (std::vector<std::string>{"empty", "file"}));
    EXPECT_EQ(read_file(path), std::string("\x00\x0A\xFF", bytes.size()));
    EXPECT_EQ(read_file(empty_path), "");
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

// Made at once, so that nothing written to it is where another may read it.
TEST(SafeWriteFileTest, OwnerAccessMakesAWritablePathOnlyItsOwnerMayUse) {
    const ScratchDirectory directory;
    const SafeWriteFile file(directory.path() / "file", SafeWriteFile::Access::Owner);

    ASSERT_TRUE(std::filesystem::exists(file.writable_path()));
    EXPECT_EQ(std::filesystem::status(file.writable_path()).permissions(), owner_only);
    EXPECT_EQ(read_file(file.writable_path()), "");
}

TEST(SafeWriteFileTest, OwnerAccessKeepsTheFileItsOwnersOnly) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";
    SafeWriteFile file(path, SafeWriteFile::Access::Owner);

    file.write("hello");

    EXPECT_EQ(names_in(directory.path()), std::vector<std::string>{"file"});
    EXPECT_EQ(read_file(path), "hello");
    EXPECT_EQ(std::filesystem::status(path).permissions(), owner_only);
}

TEST(SafeWriteFileTest, OwnerAccessWithNothingWrittenAndNotCommittedLeavesNothing) {
    const ScratchDirectory directory;

    {
        const SafeWriteFile file(directory.path() / "file", SafeWriteFile::Access::Owner);
    }

    EXPECT_TRUE(names_in(directory.path()).empty());
}

// A name as long as a file's may be leaves no room for what writable_path()
// adds to it.
TEST(SafeWriteFileTest, OwnerAccessWhereNoWritablePathCanBeMadeIsRefused) {
    constexpr std::size_t longest_name = 250;
    const ScratchDirectory directory;
    const auto path = directory.path() / std::string(longest_name, 'f');

    EXPECT_THROW(SafeWriteFile(path, SafeWriteFile::Access::Owner), std::runtime_error);
    EXPECT_TRUE(names_in(directory.path()).empty());
}

TEST(SafeWriteFileTest, CommitIfAbsentLinksWhatWasWrittenIntoPlace) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";

    {
        SafeWriteFile file(path);

        write_file(file.writable_path(), "hello");

        EXPECT_TRUE(file.commit_if_absent());
        EXPECT_EQ(names_in(directory.path()), std::vector<std::string>{"file"});
    }

    EXPECT_EQ(names_in(directory.path()), std::vector<std::string>{"file"});
    EXPECT_EQ(read_file(path), "hello");
}

// What was there before is left as it was, and what was written is removed.
TEST(SafeWriteFileTest, CommitIfAbsentLeavesTheFileThereBefore) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";

    write_file(path, "before");

    {
        SafeWriteFile file(path);

        write_file(file.writable_path(), "after");

        EXPECT_FALSE(file.commit_if_absent());
    }

    EXPECT_EQ(names_in(directory.path()), std::vector<std::string>{"file"});
    EXPECT_EQ(read_file(path), "before");
}

// Of two writes of the file at once, whichever commits first wins.
TEST(SafeWriteFileTest, OfTwoWritesIfAbsentTheFirstToCommitWins) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";
    SafeWriteFile first(path);
    SafeWriteFile second(path);

    write_file(first.writable_path(), "first");
    write_file(second.writable_path(), "second");

    EXPECT_TRUE(second.commit_if_absent());
    EXPECT_FALSE(first.commit_if_absent());
    EXPECT_EQ(read_file(path), "second");
}

TEST(SafeWriteFileTest, CommitIfAbsentWithNothingWrittenIsRefused) {
    const ScratchDirectory directory;
    SafeWriteFile file(directory.path() / "file");

    EXPECT_THROW((void)file.commit_if_absent(), std::runtime_error);
    EXPECT_TRUE(names_in(directory.path()).empty());
}

TEST(SafeWriteFileTest, CommittingIfAbsentOnceCommittedIsRefused) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";
    SafeWriteFile linked(path);
    SafeWriteFile renamed(directory.path() / "renamed");

    write_file(linked.writable_path(), "hello");
    write_file(renamed.writable_path(), "hello");
    ASSERT_TRUE(linked.commit_if_absent());
    renamed.commit();

    EXPECT_THROW((void)linked.commit_if_absent(), std::logic_error);
    EXPECT_THROW(linked.commit(), std::logic_error);
    EXPECT_THROW((void)renamed.commit_if_absent(), std::logic_error);
    EXPECT_EQ(read_file(path), "hello");
}

TEST(SafeWriteFileTest, WriteIfAbsentCommitsWhatItWritesIfAbsent) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";
    const auto there = directory.path() / "there";
    SafeWriteFile file(path);
    SafeWriteFile refused(there);

    write_file(there, "before");

    EXPECT_TRUE(file.write_if_absent("hello"));
    EXPECT_FALSE(refused.write_if_absent("after"));
    EXPECT_EQ(read_file(path), "hello");
    EXPECT_EQ(read_file(there), "before");
}
