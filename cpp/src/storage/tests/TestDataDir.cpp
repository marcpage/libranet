#include <gtest/gtest.h>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <iterator>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include "Fixtures.hpp"
#include "logger/Logger.hpp"
#include "storage/DataDir.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"
#include "storage/Zlib.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"

using fixtures::hello;
using fixtures::hello_hash;
using fixtures::KeepingOutput;
using fixtures::ScratchDirectory;
using fixtures::write_file;
using logger::Logger;
using storage::DataDir;
using storage::HashPrefix;
using storage::Identifier;
using storage::Zlib;
using types::Converter;
using types::Megabyte;

namespace {

// "world", and its SHA-256 digest, as `shasum -a 256` gives it, in lower case
// and in upper case.
constexpr auto world = "world";
constexpr auto world_hash = "486ea46224d1bb4fb680f34f7c9ad96a8f24ec88be73ea8e5a6c65260e9cb8a7";
constexpr auto world_hash_upper_case =
    "486EA46224D1BB4FB680F34F7C9AD96A8F24EC88BE73EA8E5A6C65260E9CB8A7";

// "x81", and its SHA-256 digest, which starts with the same two digits as
// hello's, so that it is kept in the directory beside hello's.
constexpr auto beside_hello = "x81";
constexpr auto beside_hello_hash =
    "2ce0a217d435d2285630ecbaf2c536accacac6bc16860cb3f55b6f27eb37ecb3";

// The SHA-256 digest of nothing.
constexpr auto empty_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

// The most a body may be, as stored (HighLevelDesign §4.3).
constexpr std::size_t most_body_size = 1 * Megabyte;

// The hashes identifiers name, in order.
std::vector<std::string> hashes_of(const DataDir::Identifiers& identifiers) {
    std::vector<std::string> hashes;

    std::transform(identifiers.begin(), identifiers.end(), std::back_inserter(hashes),
                   [](const Identifier& identifier) { return identifier.hash(); });
    std::sort(hashes.begin(), hashes.end());
    return hashes;
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

// The lines logged at Warning, or more severe, while run runs.
template<typename Function>
std::vector<std::string> warnings_while(Function run) {
    const auto lines = std::make_shared<std::vector<std::string>>();

    Logger::remove_all_outputs();
    Logger::add_output(std::make_unique<KeepingOutput>(lines));
    Logger::reset_levels(Logger::Level::Warning);
    run();
    Logger::remove_all_outputs();
    return *lines;
}

// Whether one of lines holds text.
bool warned(const std::vector<std::string>& lines, const std::string& text) {
    return std::any_of(lines.begin(), lines.end(), [&text](const std::string& line) {
        return std::string::npos != line.find(text);
    });
}

} // namespace

// Data is kept in a file named by its hash, in a directory named by the
// hash's first three characters, in a directory named by the algorithm.
TEST(DataDirTest, HasAndGetReadTheFileTheIdentifierNames) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());
    const Identifier identifier("sha256", hello_hash);

    write_file(directory.path() / "sha256" / "2cf" / hello_hash, hello);

    EXPECT_TRUE(store.has(identifier));
    EXPECT_EQ(store.get(identifier), Converter::bytes_of(hello));
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
// three characters, when they are not there yet, and leaves nothing there
// but the body, once it is renamed into place.
TEST(DataDirTest, PutStoresTheBodyUnderTheIdentifier) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());
    const Identifier identifier("sha256", hello_hash);

    store.put(identifier, Converter::bytes_of(hello));

    EXPECT_EQ(names_in(directory.path() / "sha256" / "2cf"), std::vector<std::string>{hello_hash});
    EXPECT_TRUE(store.has(identifier));
    EXPECT_EQ(store.get(identifier), Converter::bytes_of(hello));
}

// A body is kept as it was received, under the identifier of its content,
// not of the body.
TEST(DataDirTest, PutKeepsACompressedBodyAsItIs) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());
    const Identifier identifier("sha256", hello_hash);
    const auto body = Zlib::compress(Converter::bytes_of(hello), Zlib::max_level);

    store.put(identifier, body);

    EXPECT_TRUE(std::filesystem::is_regular_file(directory.path() / "sha256" / "2cf" / hello_hash));
    EXPECT_EQ(store.get(identifier), body);
}

// HighLevelDesign §4.3: content over 1 MiB is a valid object, provided it is
// stored compressed to within the limit.
TEST(DataDirTest, ContentOverAMebibyteIsKeptCompressed) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());
    const DataDir::Data content(2 * most_body_size, std::byte{0});
    const auto identifier = Identifier::of(content);
    const auto body = Zlib::compress(content, Zlib::max_level);
    const auto path = directory.path() / "sha256" / identifier.hash().substr(0, 3)
                      / identifier.hash();

    store.put(identifier, body);

    EXPECT_LT(body.size(), most_body_size);
    EXPECT_EQ(std::filesystem::file_size(path), body.size());
    EXPECT_EQ(store.get(identifier), body);
}

TEST(DataDirTest, PutMakesTheStoreDirectoryWhenItIsNotThere) {
    const ScratchDirectory directory;
    const DataDir store(directory.path() / "missing" / "store");
    const Identifier identifier("sha256", hello_hash);

    store.put(identifier, Converter::bytes_of(hello));

    EXPECT_TRUE(store.has(identifier));
    EXPECT_EQ(store.get(identifier), Converter::bytes_of(hello));
}

TEST(DataDirTest, PutNothingStoresAnEmptyFile) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());
    const Identifier identifier("sha256", empty_hash);

    store.put(identifier, {});

    EXPECT_TRUE(store.has(identifier));
    EXPECT_EQ(store.get(identifier), DataDir::Data{});
}

// As when content stored compressed is put again as it is. The second put()
// finds the directories the first one made.
TEST(DataDirTest, PutReplacesTheBodyStoredBefore) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());
    const Identifier identifier("sha256", hello_hash);

    store.put(identifier, Zlib::compress(Converter::bytes_of(hello), Zlib::max_level));
    store.put(identifier, Converter::bytes_of(hello));

    EXPECT_EQ(names_in(directory.path() / "sha256" / "2cf"), std::vector<std::string>{hello_hash});
    EXPECT_EQ(store.get(identifier), Converter::bytes_of(hello));
}

// A regular file where the directory should be: no file can be made in it.
TEST(DataDirTest, PutWhereNoFileCanBeMadeIsRefused) {
    const ScratchDirectory directory;
    const auto not_a_directory = directory.path() / "file";
    const DataDir store(not_a_directory);
    const Identifier identifier("sha256", hello_hash);

    write_file(not_a_directory, hello);

    EXPECT_THROW(store.put(identifier, Converter::bytes_of(hello)), std::runtime_error);
}

// A directory where the body goes: the body is written beside it, but
// cannot be renamed into its place, and what was written is removed.
TEST(DataDirTest, PutOverADirectoryIsRefusedAndLeavesNothingWritten) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());
    const Identifier identifier("sha256", hello_hash);

    std::filesystem::create_directories(directory.path() / "sha256" / "2cf" / hello_hash);

    EXPECT_THROW(store.put(identifier, Converter::bytes_of(hello)), std::runtime_error);
    EXPECT_EQ(names_in(directory.path() / "sha256" / "2cf"), std::vector<std::string>{hello_hash});
}

TEST(DataDirTest, RemoveTakesTheBodyAway) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());
    const Identifier identifier("sha256", hello_hash);

    store.put(identifier, Converter::bytes_of(hello));

    EXPECT_TRUE(store.remove(identifier));
    EXPECT_FALSE(store.has(identifier));
    EXPECT_FALSE(store.remove(identifier));
}

TEST(DataDirTest, RemoveLeavesADirectoryInPlaceOfTheFile) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());
    const auto path = directory.path() / "sha256" / "2cf" / hello_hash;

    std::filesystem::create_directories(path);

    EXPECT_FALSE(store.remove(Identifier("sha256", hello_hash)));
    EXPECT_TRUE(std::filesystem::is_directory(path));
}

// "hello" and "world" are five bytes each, and "x81" three.
TEST(DataDirTest, SizeIsTheBytesOfEveryBodyHeld) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());

    store.put(Identifier("sha256", hello_hash), Converter::bytes_of(hello));
    store.put(Identifier("sha256", beside_hello_hash), Converter::bytes_of(beside_hello));
    store.put(Identifier("sha256", world_hash), Converter::bytes_of(world));

    EXPECT_EQ(store.size(), 13U);
}

TEST(DataDirTest, SizeOfAStoreNotMadeYetIsNothing) {
    const ScratchDirectory directory;
    const DataDir store(directory.path() / "missing");

    EXPECT_EQ(store.size(), 0U);
}

// A file a body is being written to, and a directory of an algorithm the
// node does not know, are passed over quietly. Anything else in a directory
// of hashes that is not a body held is warned of: a name that is not a hash,
// a hash in upper case, a hash in the wrong directory, and a directory.
TEST(DataDirTest, SizeLeavesOutWhatIsNotABodyHeld) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());
    const auto sha256 = directory.path() / "sha256";
    std::uintmax_t size = 0;

    store.put(Identifier("sha256", hello_hash), Converter::bytes_of(hello));
    write_file(sha256 / "2cf" / ("." + std::string(hello_hash) + ".1.partial"), "partial");
    write_file(directory.path() / "md5" / "2cf" / hello_hash, hello);
    write_file(sha256 / "2cf" / "notes", "notes");
    write_file(sha256 / "486" / world_hash_upper_case, world);
    write_file(sha256 / "2c" / hello_hash, hello);
    std::filesystem::create_directories(sha256 / "2ce" / beside_hello_hash);

    const auto warnings = warnings_while([&] { size = store.size(); });

    EXPECT_EQ(size, 5U);
    EXPECT_EQ(warnings.size(), 4U);
    EXPECT_TRUE(warned(warnings, "notes"));
    EXPECT_TRUE(warned(warnings, world_hash_upper_case));
    EXPECT_TRUE(warned(warnings, (std::filesystem::path("2c") / hello_hash).string()));
    EXPECT_TRUE(warned(warnings, beside_hello_hash));
}

// A prefix shorter than a directory's name is looked for in each directory
// it can be in, and a longer one in the one it is in. The prefix may be in
// any case.
TEST(DataDirTest, StartingWithListsWhatBeginsWithThePrefix) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());
    const std::vector<std::string> only_hello{hello_hash};

    store.put(Identifier("sha256", hello_hash), Converter::bytes_of(hello));
    store.put(Identifier("sha256", beside_hello_hash), Converter::bytes_of(beside_hello));
    store.put(Identifier("sha256", world_hash), Converter::bytes_of(world));

    EXPECT_EQ(hashes_of(store.starting_with(HashPrefix::parse("2c"))),
              (std::vector<std::string>{beside_hello_hash, hello_hash}));
    EXPECT_EQ(hashes_of(store.starting_with(HashPrefix::parse("2cf"))), only_hello);
    EXPECT_EQ(hashes_of(store.starting_with(HashPrefix::parse("2CF24D"))), only_hello);
    EXPECT_EQ(hashes_of(store.starting_with(HashPrefix::parse(hello_hash))), only_hello);
    EXPECT_EQ(hashes_of(store.starting_with(HashPrefix::parse("4"))),
              std::vector<std::string>{world_hash});
    EXPECT_TRUE(store.starting_with(HashPrefix::parse("2cf3")).empty());
}

TEST(DataDirTest, StartingWithInAStoreNotMadeYetIsNothing) {
    const ScratchDirectory directory;
    const DataDir store(directory.path() / "missing");

    EXPECT_TRUE(store.starting_with(HashPrefix::parse("2cf")).empty());
}

// Only what is named as the prefix begins is looked at, so only what of that
// is not a body held is warned of.
TEST(DataDirTest, StartingWithLeavesOutWhatIsNotABodyHeld) {
    const ScratchDirectory directory;
    const DataDir store(directory.path());
    const auto hello_directory = directory.path() / "sha256" / "2cf";
    DataDir::Identifiers found;

    store.put(Identifier("sha256", hello_hash), Converter::bytes_of(hello));
    write_file(hello_directory / ("." + std::string(hello_hash) + ".1.partial"), "partial");
    write_file(hello_directory / "notes", "notes");
    write_file(hello_directory / "2cf24dba5f.txt", "notes");

    const auto warnings =
        warnings_while([&] { found = store.starting_with(HashPrefix::parse("2cf2")); });

    EXPECT_EQ(hashes_of(found), std::vector<std::string>{hello_hash});
    EXPECT_EQ(warnings.size(), 1U);
    EXPECT_TRUE(warned(warnings, "2cf24dba5f.txt"));
}

TEST(DataDirTest, CreateMakesOneForTheDirectory) {
    const ScratchDirectory directory;
    const auto store = DataDir::create(directory.path());
    const Identifier identifier("sha256", hello_hash);

    write_file(directory.path() / "sha256" / "2cf" / hello_hash, hello);

    ASSERT_NE(store, nullptr);
    EXPECT_TRUE(store->has(identifier));
}
