#include <gtest/gtest.h>
#include <filesystem>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>

#include "Fixtures.hpp"
#include "storage/Identifier.hpp"
#include "storage/Manager.hpp"

using fixtures::bytes_of;
using fixtures::hello;
using fixtures::hello_hash;
using fixtures::MemorySource;
using fixtures::ScratchDirectory;
using fixtures::write_file;
using storage::Identifier;
using storage::Manager;

TEST(ManagerTest, PutWithNoSourceIsRefused) {
    const auto manager = Manager::create();

    EXPECT_THROW((void)manager->put(bytes_of(hello)), std::invalid_argument);
}

TEST(ManagerTest, PutGoesToTheFirstSourceAdded) {
    const auto manager = Manager::create();
    const auto first = std::make_shared<MemorySource>();
    const auto second = std::make_shared<MemorySource>();

    manager->add(first);
    manager->add(second);
    const auto identifier = manager->put(bytes_of(hello));

    EXPECT_EQ(identifier.hash(), hello_hash);
    EXPECT_TRUE(first->has(identifier));
    EXPECT_FALSE(second->has(identifier));
}

TEST(ManagerTest, HasAndGetLookInEverySource) {
    const auto manager = Manager::create();
    const auto first = std::make_shared<MemorySource>();
    const auto second = std::make_shared<MemorySource>();

    manager->add(first);
    manager->add(second);
    const auto identifier = second->put(bytes_of(hello));

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

    EXPECT_THROW((void)manager->put(bytes_of(hello)), std::invalid_argument);
}
