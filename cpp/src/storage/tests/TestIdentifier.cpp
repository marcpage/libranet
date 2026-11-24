#include <gtest/gtest.h>
#include <algorithm>
#include <cctype>
#include <cstddef>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "Fixtures.hpp"
#include "logger/Logger.hpp"
#include "storage/Identifier.hpp"
#include "storage/Zlib.hpp"
#include "types/Json.hpp"

using fixtures::bytes_of;
using fixtures::hello_hash;
using fixtures::KeepingOutput;
using logger::Logger;
using storage::Identifier;
using storage::Zlib;
using types::Json;

namespace {

// Enough for a test's content to decompress to.
constexpr std::size_t enough = 1024;

// data as a zlib stream, compressed as much as zlib can.
Identifier::Data zlib_of(const Identifier::Data& data) {
    return Zlib::compress(data, Zlib::max_level);
}

// Whether identifier finds none of the content it names in body.
bool finds_nothing(const Identifier& identifier, const Identifier::Data& body) {
    return !identifier.content_of(body, enough).has_value();
}

// Whether Identifier::parse() turns down text as invalid.
bool refused(const std::string& text) {
    try {
        (void)Identifier::parse(text);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

// What Identifier::from_json() makes of value, as text, or "nothing".
std::string made_from(const Json& value) {
    const auto identifier = Identifier::from_json(value);

    return identifier ? identifier->to_string() : "nothing";
}

// Whether Identifier::from_json() makes nothing of value.
bool names_none(const Json& value) {
    return !Identifier::from_json(value).has_value();
}

// text, with its letters in upper case.
std::string upper_case(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(), [](char character) {
        return static_cast<char>(std::toupper(static_cast<unsigned char>(character)));
    });
    return text;
}

// Runs run, writes what it logged to stderr, and ends the program, with as
// many lines as were logged as its status.
template<typename Function>
[[noreturn]] void run_and_exit(Function run) {
    const auto lines = std::make_shared<std::vector<std::string>>();

    Logger::remove_all_outputs();
    Logger::add_output(std::make_unique<KeepingOutput>(lines));
    Logger::reset_levels(Logger::Level::Warning);
    run();
    Logger::remove_all_outputs();

    for (const auto& line : *lines) {
        std::cerr << line << '\n';
    }

    std::_Exit(static_cast<int>(lines->size()));
}

// Refuses the text of each identifier, as run_and_exit() runs it.
[[noreturn]] void refuse_and_exit(const std::vector<std::string>& texts) {
    run_and_exit([&texts] {
        for (const auto& text : texts) {
            (void)refused(text);
        }
    });
}

// Reads value as an identifier, as run_and_exit() runs it.
[[noreturn]] void read_and_exit(const Json& value) {
    run_and_exit([&value] { (void)Identifier::from_json(value); });
}

} // namespace

TEST(IdentifierTest, DefaultIsEmpty) {
    const Identifier identifier;

    EXPECT_EQ(identifier.algorithm(), "");
    EXPECT_EQ(identifier.hash(), "");
    EXPECT_EQ(identifier.to_string(), "/");
}

TEST(IdentifierTest, AlgorithmAndHash) {
    const Identifier identifier("sha256", hello_hash);

    EXPECT_EQ(identifier.algorithm(), "sha256");
    EXPECT_EQ(identifier.hash(), hello_hash);
    EXPECT_EQ(identifier.to_string(), std::string("sha256/") + hello_hash);
}

TEST(IdentifierTest, FromText) {
    const auto identifier = Identifier::parse(std::string("sha256/") + hello_hash);

    EXPECT_EQ(identifier.algorithm(), "sha256");
    EXPECT_EQ(identifier.hash(), hello_hash);
}

// Slashes before the algorithm, and whatever follows the hash, are not part
// of the identifier.
TEST(IdentifierTest, FromTextSkipsLeadingSlashesAndWhatFollows) {
    const auto identifier =
        Identifier::parse(std::string("//sha256/") + hello_hash + "/more/parts");

    EXPECT_EQ(identifier.algorithm(), "sha256");
    EXPECT_EQ(identifier.hash(), hello_hash);
}

TEST(IdentifierTest, TextWithoutAHashIsRefused) {
    EXPECT_TRUE(refused(""));
    EXPECT_TRUE(refused("///"));
    EXPECT_TRUE(refused("sha256"));
    EXPECT_TRUE(refused("/sha256"));
    EXPECT_TRUE(refused("sha256/"));
}

// An algorithm names a directory in a store, so ".." would reach the
// directory above it (issue #782).
TEST(IdentifierTest, UnsupportedAlgorithmIsRefused) {
    const std::string hash(hello_hash);

    EXPECT_TRUE(refused("../" + hash));
    EXPECT_TRUE(refused("./" + hash));
    EXPECT_TRUE(refused("/../" + hash));
    EXPECT_TRUE(refused("md5/" + hash));
    EXPECT_TRUE(refused("sha2560/" + hash));
    EXPECT_THROW((void)Identifier("..", hash), std::invalid_argument);
}

// A SHA-256 hash is 64 hex digits, two for each byte of its digest
// (HttpApi §5.4).
TEST(IdentifierTest, HashThatIsNotHexOfTheDigestsLengthIsRefused) {
    const std::string hash(hello_hash);

    EXPECT_TRUE(refused("sha256/abc123"));
    EXPECT_TRUE(refused("sha256/" + hash.substr(1)));
    EXPECT_TRUE(refused("sha256/" + hash + "0"));
    EXPECT_TRUE(refused("sha256/" + hash.substr(1) + "g"));
    EXPECT_TRUE(refused("sha256/" + hash.substr(1) + "."));
    EXPECT_TRUE(refused("sha256/" + hash.substr(1) + " "));
    EXPECT_TRUE(refused("sha256/" + hash.substr(2) + "0x"));
    EXPECT_THROW((void)Identifier("sha256", ".."), std::invalid_argument);
}

// SHA-256, the only algorithm supported, has the longest hash.
TEST(IdentifierTest, LongestHashIsSha256s) {
    EXPECT_EQ(Identifier::longest_hash_digits(), 64U);
}

// Only SHA-256 is supported, named as an identifier names it.
TEST(IdentifierTest, OnlySha256IsSupported) {
    EXPECT_TRUE(Identifier::is_supported("sha256"));
    EXPECT_FALSE(Identifier::is_supported("SHA256"));
    EXPECT_FALSE(Identifier::is_supported("md5"));
    EXPECT_FALSE(Identifier::is_supported(""));
}

// Hashes should be in lower case, but upper and mixed case are taken, and
// kept in lower case (HttpApi §5.4).
TEST(IdentifierTest, HashInUpperOrMixedCaseIsKeptInLowerCase) {
    const std::string hash(hello_hash);
    const auto mixed = hash.substr(0, 32) + upper_case(hash.substr(32));

    const auto upper = Identifier::parse("sha256/" + upper_case(hash));
    const Identifier mixed_case("sha256", mixed);

    EXPECT_EQ(upper.hash(), hello_hash);
    EXPECT_EQ(mixed_case.hash(), hello_hash);
    EXPECT_EQ(mixed_case.to_string(), "sha256/" + hash);
}

// An unknown algorithm may mean the node needs updating, so it is logged,
// but only the first, so that requests cannot fill the log. Each death
// test runs in a program of its own, which has logged nothing before.
TEST(IdentifierDeathTest, OnlyTheFirstUnsupportedAlgorithmIsLogged) {
    GTEST_FLAG_SET(death_test_style, "threadsafe");
    const std::string hash(hello_hash);

    EXPECT_EXIT(refuse_and_exit({"md5/" + hash, "md5/" + hash, "sha512/" + hash}),
                testing::ExitedWithCode(1), R"(\[WRN\].*hash algorithm md5)");
}

// A hash that is not valid is no sign of an algorithm the node lacks.
TEST(IdentifierDeathTest, HashThatIsNotValidIsNotLogged) {
    GTEST_FLAG_SET(death_test_style, "threadsafe");

    EXPECT_EXIT(refuse_and_exit({"sha256/abc123", "sha256"}), testing::ExitedWithCode(0), "");
}

// JSON text with no algorithm before its slash names none the node lacks.
TEST(IdentifierDeathTest, JsonWithNoAlgorithmIsNotLogged) {
    GTEST_FLAG_SET(death_test_style, "threadsafe");
    const auto text = std::string("/sha256/") + hello_hash;

    EXPECT_EXIT(read_and_exit(Json(text)), testing::ExitedWithCode(0), "");
}

// An identifier in JSON, as a list or a search answer names one, is a
// string of an algorithm and a hash (HttpApi §6.1).
TEST(IdentifierTest, FromJson) {
    const std::string hash(hello_hash);

    EXPECT_EQ(made_from("sha256/" + hash), "sha256/" + hash);
    EXPECT_EQ(made_from("sha256/" + upper_case(hash)), "sha256/" + hash);
}

TEST(IdentifierTest, JsonThatIsNoStringIsNothing) {
    EXPECT_TRUE(names_none(nullptr));
    EXPECT_TRUE(names_none(true));
    EXPECT_TRUE(names_none(1));
    EXPECT_TRUE(names_none(Json::Array{}));
    EXPECT_TRUE(names_none(Json::Object{}));
}

// Unlike text read from a path, JSON names an identifier and nothing more.
TEST(IdentifierTest, JsonTextThatNamesNoIdentifierIsNothing) {
    const std::string hash(hello_hash);

    EXPECT_TRUE(names_none(""));
    EXPECT_TRUE(names_none("sha256"));
    EXPECT_TRUE(names_none("sha256/"));
    EXPECT_TRUE(names_none("sha256/abc123"));
    EXPECT_TRUE(names_none("md5/" + hash));
    EXPECT_TRUE(names_none("/sha256/" + hash));
    EXPECT_TRUE(names_none("sha256/" + hash + "/more"));
}

// The SHA-256 digests are those `shasum -a 256` gives for the same bytes.
TEST(IdentifierTest, FromDataIsItsSha256) {
    const auto identifier = Identifier::of(bytes_of("hello"));
    const auto empty = Identifier::of(Identifier::Data{});

    EXPECT_EQ(identifier.to_string(),
              "sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824");
    EXPECT_EQ(empty.to_string(),
              "sha256/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
}

// Content sent as it is, with no compression.
TEST(IdentifierTest, ContentAsItIsIsGivenBack) {
    const Identifier identifier("sha256", hello_hash);
    const auto empty = Identifier::of(Identifier::Data{});

    EXPECT_EQ(identifier.content_of(bytes_of("hello"), enough), bytes_of("hello"));
    EXPECT_EQ(empty.content_of(Identifier::Data{}, enough), Identifier::Data{});
}

// Content sent as a zlib stream, at any level (HttpApi §8).
TEST(IdentifierTest, CompressedContentIsGivenBackDecompressed) {
    const Identifier identifier("sha256", hello_hash);
    const auto empty = Identifier::of(Identifier::Data{});

    for (auto level = Zlib::min_level; level <= Zlib::max_level; ++level) {
        const auto compressed = Zlib::compress(bytes_of("hello"), level);

        EXPECT_EQ(identifier.content_of(compressed, enough), bytes_of("hello")) << level;
    }

    EXPECT_EQ(empty.content_of(zlib_of(Identifier::Data{}), 0), Identifier::Data{});
}

// Content that is itself a zlib stream is named by the hash of the stream,
// and is given back as it is, not decompressed.
TEST(IdentifierTest, ContentThatIsAZlibStreamIsGivenBackAsItIs) {
    const auto compressed = zlib_of(bytes_of("hello"));
    const auto identifier = Identifier::of(compressed);

    EXPECT_EQ(identifier.content_of(compressed, enough), compressed);
}

// The limit bounds only what a stream decompresses to.
TEST(IdentifierTest, ContentAsItIsIsNotLimited) {
    const Identifier identifier("sha256", hello_hash);

    EXPECT_EQ(identifier.content_of(bytes_of("hello"), 0), bytes_of("hello"));
}

TEST(IdentifierTest, CompressedContentPastTheLimitIsNothing) {
    const auto content = bytes_of(std::string(enough, 'x'));
    const auto identifier = Identifier::of(content);
    const auto compressed = zlib_of(content);

    EXPECT_EQ(identifier.content_of(compressed, enough), content);
    EXPECT_FALSE(identifier.content_of(compressed, enough - 1).has_value());
}

// Neither the content, nor one zlib stream of it (HighLevelDesign §4.1.1).
// A stream is decompressed once, so a stream of a stream of the content is
// not it.
TEST(IdentifierTest, OtherContentIsNothing) {
    const Identifier identifier("sha256", hello_hash);
    const auto compressed = zlib_of(bytes_of("hello"));
    auto with_more = compressed;

    with_more.push_back(std::byte{0});

    EXPECT_TRUE(finds_nothing(identifier, bytes_of("world")));
    EXPECT_TRUE(finds_nothing(identifier, bytes_of("hello!")));
    EXPECT_TRUE(finds_nothing(identifier, Identifier::Data{}));
    EXPECT_TRUE(finds_nothing(identifier, zlib_of(bytes_of("world"))));
    EXPECT_TRUE(finds_nothing(identifier, with_more));
    EXPECT_TRUE(finds_nothing(identifier, zlib_of(compressed)));
}

// An identifier made with no algorithm or hash names no content.
TEST(IdentifierTest, DefaultNamesNoContent) {
    const Identifier identifier;

    EXPECT_TRUE(finds_nothing(identifier, Identifier::Data{}));
    EXPECT_TRUE(finds_nothing(identifier, zlib_of(Identifier::Data{})));
}

TEST(IdentifierTest, CopyAndMove) {
    Identifier original("sha256", hello_hash);
    const auto text = std::string("sha256/") + hello_hash;

    Identifier copy(original);
    Identifier assigned;
    assigned = copy;
    Identifier moved(std::move(copy));
    Identifier move_assigned;
    move_assigned = std::move(assigned);

    EXPECT_EQ(original.to_string(), text);
    EXPECT_EQ(moved.to_string(), text);
    EXPECT_EQ(move_assigned.to_string(), text);
}
