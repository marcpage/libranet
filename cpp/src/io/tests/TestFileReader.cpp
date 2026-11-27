#include <gtest/gtest.h>
#include <array>
#include <cstddef>
#include <filesystem>
#include <fstream>
#include <ios>
#include <iterator>
#include <memory>
#include <optional>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>

#include "io/FileReader.hpp"
#include "io/IReader.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"

using io::FileReader;
using io::IReader;
using types::Converter;

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

// The file at path, with text in it.
void write_file(const std::filesystem::path& path, std::string_view text) {
    std::ofstream file(path, std::ios::binary | std::ios::trunc);

    file << text;
}

// Each byte from 0 to 255, so that none is changed on the way, and so that
// what is read is not mistaken for text.
std::string every_byte() {
    constexpr std::size_t byte_values = 256;
    std::string text(byte_values, '\0');

    for (std::size_t value = 0; value < byte_values; ++value) {
        text.at(value) = static_cast<char>(value);
    }

    return text;
}

} // namespace

TEST(FileReaderTest, ReadAllReadsTheWholeFile) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";

    write_file(path, every_byte());

    EXPECT_EQ(FileReader(path).read_all(), every_byte());
}

TEST(FileReaderTest, ReadAllOfAnEmptyFileIsEmpty) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";

    write_file(path, "");

    EXPECT_EQ(FileReader(path).read_all(), "");
}

// Read a piece at a time, a file of more than one piece is read whole, as is
// one that ends where a piece does.
TEST(FileReaderTest, ReadAllReadsAFileOfManyPieces) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";

    for (const auto size : {types::piece_size, (2 * types::piece_size) + 3}) {
        const std::string text(size, '$');

        write_file(path, text);

        EXPECT_EQ(FileReader(path).read_all(), text) << size;
    }
}

TEST(FileReaderTest, ReadAllReadsWhatIsLeft) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";

    write_file(path, "first\r\nsecond\nthird");

    FileReader reader(path);

    EXPECT_EQ(reader.read_line(IReader::EolAction::MustHaveEol), "first");
    EXPECT_EQ(reader.read_all(), "second\nthird");
    EXPECT_EQ(reader.read_all(), "");
    EXPECT_EQ(reader.read_some(nullptr, 0), std::size_t{0});
}

TEST(FileReaderTest, ReadSomeReadsToTheEndThenNothing) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";
    std::array<std::byte, 3> data{};

    write_file(path, "hello");

    FileReader reader(path);

    EXPECT_EQ(reader.read_some(data.data(), data.size()), std::size_t{3});
    EXPECT_EQ(Converter::text_of({data.begin(), data.end()}), "hel");
    EXPECT_EQ(reader.read_some(data.data(), data.size()), std::size_t{2});
    EXPECT_EQ(Converter::text_of({data.begin(), std::next(data.begin(), 2)}), "lo");
    EXPECT_EQ(reader.read_some(data.data(), data.size()), std::nullopt);
    EXPECT_EQ(reader.read_some(data.data(), data.size()), std::nullopt);
}

// Asked for nothing, it reads nothing, even at the end of the file.
TEST(FileReaderTest, ReadSomeOfNothingReadsNothing) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";

    write_file(path, "");

    FileReader reader(path);

    EXPECT_EQ(reader.read_some(nullptr, 0), std::size_t{0});
}

// As any other IReader, through the methods it has from IReader.
TEST(FileReaderTest, ReadsAsAnIReader) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";

    write_file(path, "line\nfield;rest of it");

    const IReader::Ptr reader = std::make_unique<FileReader>(path);

    EXPECT_EQ(reader->read_line(IReader::EolAction::MustHaveEol), "line");
    EXPECT_EQ(reader->read_until(';'), "field;");
    EXPECT_EQ(Converter::text_of(reader->read_exact(4)), "rest");
    EXPECT_EQ(Converter::text_of(reader->read_exact(100)), " of it");
    EXPECT_EQ(reader->read_line(IReader::EolAction::MayHaveEol), "");
    EXPECT_EQ(reader->read_line(IReader::EolAction::MustHaveEol), std::nullopt);
}

TEST(FileReaderTest, AMissingFileIsRefused) {
    const ScratchDirectory directory;

    // Mistaken: it is made only to see that making it throws.
    // cppcheck-suppress unusedScopedObject
    EXPECT_THROW(FileReader(directory.path() / "missing"), std::runtime_error);
}

// Not read as an empty file, as some systems would.
TEST(FileReaderTest, ADirectoryIsRefused) {
    const ScratchDirectory directory;

    EXPECT_THROW(FileReader(directory.path()), std::runtime_error);
}

// What it reads is the file as it was opened, though the file be removed.
TEST(FileReaderTest, ReadsAFileRemovedOnceOpened) {
    const ScratchDirectory directory;
    const auto path = directory.path() / "file";

    write_file(path, "kept");

    FileReader reader(path);

    std::filesystem::remove(path);

    EXPECT_EQ(reader.read_all(), "kept");
}
