#include <gtest/gtest.h>
#include <filesystem>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>

#include "Fixtures.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"
#include "storage/Manager.hpp"

using fixtures::bytes_of;
using fixtures::hello;
using fixtures::hello_hash;
using fixtures::MemorySource;
using fixtures::ScratchDirectory;
using fixtures::write_file;
using storage::HashPrefix;
using storage::Identifier;
using storage::Manager;

namespace {

// The SHA-256 digest of nothing, as `shasum -a 256` gives it.
constexpr auto empty_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

} // namespace

TEST(ManagerTest, PutWithNoSourceIsRefused) {
    const auto manager = Manager::create();
    const Identifier identifier("sha256", hello_hash);

    EXPECT_THROW(manager->put(identifier, bytes_of(hello)), std::invalid_argument);
}

TEST(ManagerTest, PutGoesToTheFirstSourceAdded) {
    const auto manager = Manager::create();
    const auto first = std::make_shared<MemorySource>();
    const auto second = std::make_shared<MemorySource>();
    const Identifier identifier("sha256", hello_hash);

    manager->add(first);
    manager->add(second);
    manager->put(identifier, bytes_of(hello));

    EXPECT_EQ(first->get(identifier), bytes_of(hello));
    EXPECT_FALSE(second->has(identifier));
}

TEST(ManagerTest, HasAndGetLookInEverySource) {
    const auto manager = Manager::create();
    const auto first = std::make_shared<MemorySource>();
    const auto second = std::make_shared<MemorySource>();
    const Identifier identifier("sha256", hello_hash);

    manager->add(first);
    manager->add(second);
    second->put(identifier, bytes_of(hello));

    EXPECT_TRUE(manager->has(identifier));
    EXPECT_EQ(manager->get(identifier), bytes_of(hello));
}

TEST(ManagerTest, DataInNoSourceIsNotThere) {
    const auto manager = Manager::create();
    const Identifier identifier("sha256", hello_hash);

    manager->add(std::make_shared<MemorySource>());

    EXPECT_FALSE(manager->has(identifier));
    EXPECT_EQ(manager->get(identifier), std::nullopt);
}

TEST(ManagerTest, AddDirectoryReadsFromIt) {
    const ScratchDirectory directory;
    const auto manager = Manager::create();
    const Identifier identifier("sha256", hello_hash);

    write_file(directory.path() / "sha256" / "2cf" / hello_hash, hello);
    manager->add(directory.path());

    EXPECT_TRUE(manager->has(identifier));
    EXPECT_EQ(manager->get(identifier), bytes_of(hello));
}

TEST(ManagerTest, ScanAddsEachDirectoryInIt) {
    const ScratchDirectory directory;
    const auto manager = Manager::create();
    const Identifier identifier("sha256", hello_hash);

    std::filesystem::create_directories(directory.path() / "first");
    write_file(directory.path() / "second" / "sha256" / "2cf" / hello_hash, hello);
    write_file(directory.path() / "not-a-directory", hello);
    manager->scan(directory.path());

    EXPECT_TRUE(manager->has(identifier));
    EXPECT_EQ(manager->get(identifier), bytes_of(hello));
}

TEST(ManagerTest, ScanOfAMissingDirectoryAddsNothing) {
    const ScratchDirectory directory;
    const auto manager = Manager::create();

    manager->scan(directory.path() / "missing");

    EXPECT_THROW(manager->put(Identifier("sha256", hello_hash), bytes_of(hello)),
                 std::invalid_argument);
}

// So that what is removed is not still found in another source.
TEST(ManagerTest, RemoveTakesFromEverySource) {
    const auto manager = Manager::create();
    const auto first = std::make_shared<MemorySource>();
    const auto second = std::make_shared<MemorySource>();
    const Identifier identifier("sha256", hello_hash);

    manager->add(first);
    manager->add(second);
    first->put(identifier, bytes_of(hello));
    second->put(identifier, bytes_of(hello));

    EXPECT_TRUE(manager->remove(identifier));
    EXPECT_FALSE(manager->has(identifier));
    EXPECT_FALSE(manager->remove(identifier));
}

TEST(ManagerTest, RemoveFromNoSourceRemovesNothing) {
    const auto manager = Manager::create();

    EXPECT_FALSE(manager->remove(Identifier("sha256", hello_hash)));
}

// Each source's bodies count, though two hold the same content.
TEST(ManagerTest, SizeIsWhatEverySourceHolds) {
    const auto manager = Manager::create();
    const auto first = std::make_shared<MemorySource>();
    const auto second = std::make_shared<MemorySource>();
    const Identifier identifier("sha256", hello_hash);

    manager->add(first);
    manager->add(second);
    first->put(identifier, bytes_of(hello));
    second->put(identifier, bytes_of("hello, compressed"));

    EXPECT_EQ(manager->size(), 22U);
}

TEST(ManagerTest, SizeOfNoSourceIsNothing) {
    EXPECT_EQ(Manager::create()->size(), 0U);
}

TEST(ManagerTest, StartingWithNamesWhatEverySourceHoldsOnce) {
    const auto manager = Manager::create();
    const auto first = std::make_shared<MemorySource>();
    const auto second = std::make_shared<MemorySource>();
    const Identifier hello_identifier("sha256", hello_hash);
    const Identifier empty_identifier("sha256", empty_hash);

    manager->add(first);
    manager->add(second);
    first->put(hello_identifier, bytes_of(hello));
    second->put(hello_identifier, bytes_of(hello));
    second->put(empty_identifier, {});

    const auto hello_found = manager->starting_with(HashPrefix::parse("2cf"));
    const auto empty_found = manager->starting_with(HashPrefix::parse("e3"));

    ASSERT_EQ(hello_found.size(), 1U);
    EXPECT_EQ(hello_found.front().hash(), hello_hash);
    ASSERT_EQ(empty_found.size(), 1U);
    EXPECT_EQ(empty_found.front().hash(), empty_hash);
    EXPECT_TRUE(manager->starting_with(HashPrefix::parse("0")).empty());
}
