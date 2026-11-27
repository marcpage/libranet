#include <gtest/gtest.h>
#include <filesystem>
#include <system_error>

#include "Fixtures.hpp"
#include "logger/FileOutput.hpp"

using fixtures::read_file;
using fixtures::ScratchFile;
using logger::FileOutput;
using std::errc;
using std::system_error;
using std::filesystem::perms;

TEST(FileOutputTest, MakesTheFile) {
    const ScratchFile file;

    const FileOutput output(file.path());

    EXPECT_TRUE(std::filesystem::exists(file.path()));
}

TEST(FileOutputTest, OnlyTheOwnerCanReadOrWriteAFileItMakes) {
    const ScratchFile file;

    const FileOutput output(file.path());

    EXPECT_EQ(std::filesystem::status(file.path()).permissions(),
              perms::owner_read | perms::owner_write);
}

TEST(FileOutputTest, AddsToTheEndOfTheFile) {
    const ScratchFile file;

    FileOutput(file.path()).log("first\n");
    FileOutput(file.path()).log("second\n");

    EXPECT_EQ(read_file(file.path()), "first\nsecond\n");
}

TEST(FileOutputTest, FileThatCannotBeOpenedThrows) {
    const ScratchFile directory;
    const auto path = directory.path() / "missing" / "libranet.log";

    try {
        const FileOutput output(path);
        FAIL() << "Opened a file in a directory that is not there";
    } catch (const system_error& error) {
        EXPECT_EQ(error.code(), errc::no_such_file_or_directory);
    }
}
