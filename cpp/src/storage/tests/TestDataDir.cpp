#include <gtest/gtest.h>
#include <filesystem>
#include <optional>
#include <stdexcept>
#include <string>

#include "Fixtures.hpp"
#include "storage/DataDir.hpp"
#include "storage/Identifier.hpp"

using fixtures::bytes_of;
using fixtures::hello;
using fixtures::hello_hash;
using fixtures::ScratchDirectory;
using fixtures::write_file;
using storage::DataDir;
using storage::Identifier;

// Data is kept in a file named by its hash, in a directory named by the
// hash's first three characters, in a directory named by the algorithm.
TEST(DataDirTest, HasAndGetReadTheFileTheIdentifierNames) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());
    const Identifier identifier("sha256", hello_hash);

    write_file(directory.path() / "sha256" / "2cf" / hello_hash, hello);

    EXPECT_TRUE(store.has(identifier));
    EXPECT_EQ(store.get(identifier), bytes_of(hello));
}

TEST(DataDirTest, MissingDataIsNotThere) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());
    const Identifier identifier("sha256", hello_hash);

    EXPECT_FALSE(store.has(identifier));
    EXPECT_EQ(store.get(identifier), std::nullopt);
}

TEST(DataDirTest, DirectoryInPlaceOfTheFileIsNotData) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());
    const Identifier identifier("sha256", hello_hash);

    std::filesystem::create_directories(directory.path() / "sha256" / "2cf" / hello_hash);

    EXPECT_FALSE(store.has(identifier));
    EXPECT_EQ(store.get(identifier), std::nullopt);
}

// put() makes the directories named by the algorithm and by the hash's first
// three characters, when they are not there yet.
TEST(DataDirTest, PutStoresDataUnderItsSha256) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());

    const auto identifier = store.put(bytes_of(hello));

    EXPECT_EQ(identifier.to_string(), std::string("sha256/") + hello_hash);
    EXPECT_TRUE(std::filesystem::is_regular_file(directory.path() / "sha256" / "2cf" / hello_hash));
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

// A regular file where the directory should be: no file can be made in it.
TEST(DataDirTest, PutWhereNoFileCanBeMadeIsRefused) {
    const ScratchDirectory directory;
    const auto not_a_directory = directory.path() / "file";
    const DataDir store(not_a_directory);

    write_file(not_a_directory, hello);

    EXPECT_THROW((void)store.put(bytes_of(hello)), std::runtime_error);
}

TEST(DataDirTest, CreateMakesOneForTheDirectory) {
    const ScratchDirectory directory;
    const auto store = DataDir::create(directory.path());
    const Identifier identifier("sha256", hello_hash);

    write_file(directory.path() / "sha256" / "2cf" / hello_hash, hello);

    ASSERT_NE(store, nullptr);
    EXPECT_TRUE(store->has(identifier));
}
