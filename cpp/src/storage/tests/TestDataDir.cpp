#include <gtest/gtest.h>
#include <algorithm>
#include <cstddef>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>

#include "storage/DataDir.hpp"
#include "storage/Identifier.hpp"

using storage::DataDir;

namespace {

// "hello", and its SHA-256 digest, as `shasum -a 256` gives it.
constexpr std::string_view hello = "hello";
constexpr std::string_view hello_hash = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";

// The bytes of text, a byte for each char.
DataDir::Data bytes_of(std::string_view text) {
    DataDir::Data bytes;

    bytes.reserve(text.size());
    std::transform(text.begin(), text.end(), std::back_inserter(bytes), [](char character) {
        return static_cast<std::byte>(character);
    });
    return bytes;
}

// A directory of the running test's own, made under the system's directory
// for temporary files, and removed with all it holds when this is destroyed.
// Its name holds the test's and a random number, so that two runs of the
// tests at once do not share one.
class ScratchDirectory {
public:
    ScratchDirectory();
    ~ScratchDirectory();

    [[nodiscard]] const std::filesystem::path& path() const noexcept;

    ScratchDirectory(const ScratchDirectory&) = delete;
    ScratchDirectory& operator=(const ScratchDirectory&) = delete;
    ScratchDirectory(ScratchDirectory&&) = delete;
    ScratchDirectory& operator=(ScratchDirectory&&) = delete;

private:
    std::filesystem::path m_path;
};

ScratchDirectory::ScratchDirectory() {
    const auto* const test = testing::UnitTest::GetInstance()->current_test_info();
    std::random_device random;

    m_path = std::filesystem::temp_directory_path()
             / (std::string("libranet-") + test->test_suite_name() + "-" + test->name() + "-"
                + std::to_string(random()));
    std::filesystem::create_directories(m_path);
}

ScratchDirectory::~ScratchDirectory() {
    std::error_code error;

    std::filesystem::remove_all(m_path, error);
}

const std::filesystem::path& ScratchDirectory::path() const noexcept {
    return m_path;
}

} // namespace

// put() makes the directories named by the algorithm and by the hash's first
// three characters, when they are not there yet.
TEST(DataDirTest, PutMakesTheDirectoriesItWritesInto) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());

    const auto identifier = store.put(bytes_of(hello));

    EXPECT_EQ(identifier.to_string(), "sha256/" + std::string(hello_hash));
    EXPECT_TRUE(std::filesystem::is_regular_file(directory.path() / "sha256" / "2cf" / std::string(hello_hash)));
    EXPECT_TRUE(store.has(identifier));
    EXPECT_EQ(store.get(identifier), bytes_of(hello));
}

TEST(DataDirTest, PutMakesTheStoreDirectoryWhenItIsNotThere) {
    const ScratchDirectory directory;
    const DataDir store(directory.path() / "missing" / "store");

    const auto identifier = store.put(bytes_of(hello));

    EXPECT_TRUE(store.has(identifier));
    EXPECT_EQ(store.get(identifier), bytes_of(hello));
}

TEST(DataDirTest, PutNothingStoresAnEmptyFile) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());

    const auto identifier = store.put({});

    EXPECT_EQ(identifier.hash(), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    EXPECT_EQ(store.get(identifier), DataDir::Data{});
}

// The second put() finds the directories the first one made.
TEST(DataDirTest, PutTheSameDataTwiceKeepsIt) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());

    const auto first = store.put(bytes_of(hello));
    const auto second = store.put(bytes_of(hello));

    EXPECT_EQ(first.to_string(), second.to_string());
    EXPECT_EQ(store.get(second), bytes_of(hello));
}

// A regular file where the store's directory should be: no directory can be
// made in it.
TEST(DataDirTest, PutWhereNoDirectoryCanBeMadeIsRefused) {
    const ScratchDirectory directory;
    const auto not_a_directory = directory.path() / "file";
    const DataDir store(not_a_directory);

    std::ofstream(not_a_directory) << hello;

    EXPECT_THROW((void)store.put(bytes_of(hello)), std::runtime_error);
}
