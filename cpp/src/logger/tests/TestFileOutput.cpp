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

TEST(FileOutputTest, MakesTheFile) {
    const ScratchFile file;

    const FileOutput output(file.path().string());

    EXPECT_TRUE(std::filesystem::exists(file.path()));
}

TEST(FileOutputTest, AddsToTheEndOfTheFile) {
    const ScratchFile file;

    FileOutput(file.path().string()).log("first\n");
    FileOutput(file.path().string()).log("second\n");

    EXPECT_EQ(read_file(file.path()), "first\nsecond\n");
}

TEST(FileOutputTest, FileThatCannotBeOpenedThrows) {
    const ScratchFile directory;
    const auto path = directory.path() / "missing" / "libranet.log";

    try {
        const FileOutput output(path.string());
        FAIL() << "Opened a file in a directory that is not there";
    } catch (const system_error& error) {
        EXPECT_EQ(error.code(), errc::no_such_file_or_directory);
    }
}
