#include <gtest/gtest.h>
#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "Fixtures.hpp"
#include "logger/ILogOutput.hpp"
#include "logger/Logger.hpp"
#include "storage/Identifier.hpp"

using fixtures::bytes_of;
using fixtures::hello_hash;
using logger::ILogOutput;
using logger::Logger;
using storage::Identifier;

namespace {

// Whether Identifier::from() turns down text as invalid.
bool refused(const std::string& text) {
    try {
        (void)Identifier::from(text);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

// text, with its letters in upper case.
std::string upper_case(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(), [](char character) {
        return static_cast<char>(std::toupper(static_cast<unsigned char>(character)));
    });
    return text;
}

// Keeps each line it is given, where a test can read them while the Logger
// owns it.
class KeepingOutput : public ILogOutput {
public:
    explicit KeepingOutput(std::shared_ptr<std::vector<std::string>> lines)
        : m_lines(std::move(lines)) {
    }

    void log(std::string_view line) override {
        m_lines->emplace_back(line);
    }

private:
    std::shared_ptr<std::vector<std::string>> m_lines;
};

// Refuses the text of each identifier, writes what was logged to stderr,
// and ends the program, with as many as were logged as its status.
[[noreturn]] void refuse_and_exit(const std::vector<std::string>& texts) {
    const auto lines = std::make_shared<std::vector<std::string>>();

    Logger::remove_all_outputs();
    Logger::add_output(std::make_unique<KeepingOutput>(lines));
    Logger::reset_levels(Logger::Level::Warning);

    for (const auto& text : texts) {
        (void)refused(text);
    }

    Logger::remove_all_outputs();

    for (const auto& line : *lines) {
        std::cerr << line << '\n';
    }

    std::_Exit(static_cast<int>(lines->size()));
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
    const auto identifier = Identifier::from(std::string("sha256/") + hello_hash);

    EXPECT_EQ(identifier.algorithm(), "sha256");
    EXPECT_EQ(identifier.hash(), hello_hash);
}

// Slashes before the algorithm, and whatever follows the hash, are not part
// of the identifier.
TEST(IdentifierTest, FromTextSkipsLeadingSlashesAndWhatFollows) {
    const auto identifier =
        Identifier::from(std::string("//sha256/") + hello_hash + "/more/parts");

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

// Hashes should be in lower case, but upper and mixed case are taken, and
// kept in lower case (HttpApi §5.4).
TEST(IdentifierTest, HashInUpperOrMixedCaseIsKeptInLowerCase) {
    const std::string hash(hello_hash);
    const auto mixed = hash.substr(0, 32) + upper_case(hash.substr(32));

    const auto upper = Identifier::from("sha256/" + upper_case(hash));
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

// The SHA-256 digests are those `shasum -a 256` gives for the same bytes.
TEST(IdentifierTest, FromDataIsItsSha256) {
    const auto identifier = Identifier::from(bytes_of("hello"));
    const auto empty = Identifier::from(Identifier::Data{});

    EXPECT_EQ(identifier.to_string(),
              "sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824");
    EXPECT_EQ(empty.to_string(),
              "sha256/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
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
