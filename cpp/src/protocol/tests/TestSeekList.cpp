#include <gtest/gtest.h>
#include <algorithm>
#include <cstddef>
#include <cstdlib>
#include <iostream>
#include <iterator>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "logger/ILogOutput.hpp"
#include "logger/Logger.hpp"
#include "protocol/SeekList.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"
#include "storage/Zlib.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"

using logger::ILogOutput;
using logger::Logger;
using protocol::SeekList;
using storage::HashPrefix;
using storage::Identifier;
using storage::Zlib;
using types::Converter;
using types::Megabyte;

namespace {

// "hello" and nothing, hashed with SHA-256, as `shasum -a 256` gives them.
constexpr auto hello_hash = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";
constexpr auto empty_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

// More than any list here holds once decompressed, as the Python node's
// storage.max_decompressed_list_bytes is.
constexpr std::size_t decompressed_limit = 4 * Megabyte;

// text as one zlib stream.
std::string compressed(std::string_view text) {
    return Converter::text_of(Zlib::compress(Converter::bytes_of(text), Zlib::max_level));
}

// The text of the SHA-256 identifier of hash.
std::string sha256(std::string_view hash) {
    return "sha256/" + std::string(hash);
}

// The text of the SHA-256 identifier of hash, as a JSON string.
std::string quoted(std::string_view hash) {
    return '"' + sha256(hash) + '"';
}

// The list text holds, as JSON.
SeekList read_text(std::string_view text) {
    return SeekList::from_body(text, decompressed_limit);
}

// Whether SeekList::from_body() turns down body as not a seek list.
bool refused(std::string_view body) {
    try {
        (void)SeekList::from_body(body, decompressed_limit);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

// The text of each entry.
template<typename Entry>
std::vector<std::string> texts(const std::vector<Entry>& entries) {
    std::vector<std::string> result;

    std::transform(entries.begin(), entries.end(), std::back_inserter(result),
                   [](const Entry& entry) { return entry.to_string(); });
    return result;
}

// The first count of entries.
template<typename Entry>
std::vector<Entry> first(const std::vector<Entry>& entries, std::size_t count) {
    return {entries.begin(), std::next(entries.begin(), static_cast<std::ptrdiff_t>(count))};
}

// count identifiers, each different: those of the text of 0, 1, 2, and on.
std::vector<Identifier> identifiers(std::size_t count) {
    std::vector<Identifier> result;
    result.reserve(count);

    for (std::size_t index = 0; index < count; ++index) {
        result.push_back(Identifier::of(Converter::bytes_of(std::to_string(index))));
    }

    return result;
}

// count prefixes, each different: the whole hashes of identifiers(count).
std::vector<HashPrefix> prefixes(std::size_t count) {
    const auto hashed = identifiers(count);
    std::vector<HashPrefix> result;

    std::transform(hashed.begin(), hashed.end(), std::back_inserter(result),
                   [](const Identifier& identifier) {
        return HashPrefix::parse(identifier.hash());
    });
    return result;
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

// Reads text as a list, writes what was logged at Debug and above to
// stderr, and ends the program, with as many lines as were logged as its
// status.
[[noreturn]] void read_and_exit(std::string_view text) {
    const auto lines = std::make_shared<std::vector<std::string>>();

    Logger::remove_all_outputs();
    Logger::add_output(std::make_unique<KeepingOutput>(lines));
    Logger::reset_levels(Logger::Level::Debug);
    (void)read_text(text);
    Logger::remove_all_outputs();

    for (const auto& line : *lines) {
        std::cerr << line << '\n';
    }

    std::_Exit(static_cast<int>(lines->size()));
}

} // namespace

// The example HttpApi §10.7.1 gives.
TEST(SeekListTest, DataAndSearch) {
    const auto list = read_text(R"({"data": [)" + quoted(empty_hash)
                                + R"(], "search": ["0123456789abcdef"]})");

    EXPECT_EQ(texts(list.data()), std::vector<std::string>{sha256(empty_hash)});
    EXPECT_EQ(texts(list.search()), std::vector<std::string>{"0123456789abcdef"});
}

TEST(SeekListTest, DefaultIsEmpty) {
    const SeekList list;

    EXPECT_TRUE(list.data().empty());
    EXPECT_TRUE(list.search().empty());
    EXPECT_EQ(list.body().text, R"({"data":[],"search":[]})");
}

// A kind the list does not name is none sought, and a member HttpApi
// §10.7.1 does not name is ignored, as the Python node does.
TEST(SeekListTest, KindNotNamedIsNoneSought) {
    const auto empty = read_text("{}");
    const auto only_search = read_text(R"({"search": ["2cf2"], "other": 1})");

    EXPECT_TRUE(empty.data().empty());
    EXPECT_TRUE(empty.search().empty());
    EXPECT_TRUE(only_search.data().empty());
    EXPECT_EQ(texts(only_search.search()), std::vector<std::string>{"2cf2"});
}

// Hashes should be in lower case, but upper case is taken, and kept in
// lower case (HttpApi §5.4).
TEST(SeekListTest, UpperCaseIsKeptInLowerCase) {
    const auto list = read_text(R"({"data": ["sha256/2CF24DBA5FB0A30E26E83B2AC5B9E29E)"
                                R"(1B161E5C1FA7425E73043362938B9824"], "search": ["2CF2"]})");

    EXPECT_EQ(texts(list.data()), std::vector<std::string>{sha256(hello_hash)});
    EXPECT_EQ(texts(list.search()), std::vector<std::string>{"2cf2"});
}

TEST(SeekListTest, NotASeekListIsRefused) {
    EXPECT_TRUE(refused(""));
    EXPECT_TRUE(refused("not JSON"));
    EXPECT_TRUE(refused(R"({"data": [})"));
    EXPECT_TRUE(refused("null"));
    EXPECT_TRUE(refused("[]"));
    EXPECT_TRUE(refused(R"("data")"));
    EXPECT_TRUE(refused(R"({"data": {}})"));
    EXPECT_TRUE(refused(R"({"data": )" + quoted(hello_hash) + "}"));
    EXPECT_TRUE(refused(R"({"search": null})"));
    EXPECT_TRUE(refused(R"({"data": [], "search": 1})"));
}

// An entry that cannot be read is left out, so that it does not cost the
// peer the rest of its list. An identifier is an algorithm and a hash, and
// nothing more.
TEST(SeekListTest, EntryThatCannotBeReadIsLeftOut) {
    const auto list = read_text(
        R"({"data": [1, null, "sha256", "sha256/abc", "md5/)" + std::string(hello_hash)
        + R"(", "/)" + sha256(hello_hash) + R"(", ")" + sha256(hello_hash) + R"(/", )"
        + quoted(empty_hash) + ", [" + quoted(hello_hash) + "]],"
        + R"("search": ["", "xyz", 12, "0x12", "2cf2", ")" + hello_hash + R"(0"]})");

    EXPECT_EQ(texts(list.data()), std::vector<std::string>{sha256(empty_hash)});
    EXPECT_EQ(texts(list.search()), std::vector<std::string>{"2cf2"});
}

// What is left out is logged once for the list, for each kind with any left
// out. Each death test runs in a program of its own, which has logged
// nothing before.
TEST(SeekListDeathTest, WhatIsLeftOutIsLoggedOnceForEachKind) {
    GTEST_FLAG_SET(death_test_style, "threadsafe");

    EXPECT_EXIT(read_and_exit(R"({"data": [1, 2, "sha256/abc"], "search": ["xyz", "2cf2"]})"),
                testing::ExitedWithCode(2),
                R"(\[DBG\].*3 of the 3 entries of a seek list's data.*)"
                R"(\[DBG\].*1 of the 2 entries of a seek list's search)");
}

TEST(SeekListDeathTest, NothingLeftOutIsNotLogged) {
    GTEST_FLAG_SET(death_test_style, "threadsafe");

    EXPECT_EXIT(read_and_exit(R"({"data": [)" + quoted(hello_hash) + R"(], "search": []})"),
                testing::ExitedWithCode(0), "");
}

// A list may be sent zlib-compressed (HttpApi §10.7.1).
TEST(SeekListTest, ZlibCompressedIsRead) {
    const auto json = R"({"data": [)" + quoted(hello_hash) + R"(], "search": ["2cf2"]})";

    const auto list = SeekList::from_body(compressed(json), decompressed_limit);

    EXPECT_EQ(texts(list.data()), std::vector<std::string>{sha256(hello_hash)});
    EXPECT_EQ(texts(list.search()), std::vector<std::string>{"2cf2"});
}

// A small body could expand without bound, so one that holds more than the
// limit once decompressed is refused.
TEST(SeekListTest, MoreThanTheLimitDecompressedIsRefused) {
    constexpr std::string_view json = R"({"data":[],"search":[]})";
    const auto body = compressed(json);

    EXPECT_NO_THROW((void)SeekList::from_body(body, json.size()));
    EXPECT_THROW((void)SeekList::from_body(body, json.size() - 1), std::invalid_argument);
}

TEST(SeekListTest, CompressedNotASeekListIsRefused) {
    auto truncated = compressed("{}");

    truncated.pop_back();

    EXPECT_TRUE(refused(compressed("not JSON")));
    EXPECT_TRUE(refused(compressed("[]")));
    EXPECT_TRUE(refused(compressed(R"({"data": 1})")));
    EXPECT_TRUE(refused(truncated));
}

TEST(SeekListTest, WrittenAsCompactJson) {
    const SeekList list({Identifier("sha256", hello_hash)}, {HashPrefix::parse("2CF2")});

    EXPECT_EQ(list.body().text, R"({"data":[)" + quoted(hello_hash) + R"(],"search":["2cf2"]})");
}

// Mistaken: cppcheck takes the prefixes given to SeekList's constructor for
// a view the list keeps of them, and so the list made from prefixes(count)
// for one that outlives them. The constructor takes each vector by value,
// and the list keeps its own.
// cppcheck-suppress-begin danglingTemporaryLifetime

// What is written reads back as the same list, in the same order.
TEST(SeekListTest, WhatIsWrittenReadsBack) {
    constexpr std::size_t count = 10;
    const SeekList list(identifiers(count), prefixes(count));

    const auto reread = SeekList::from_body(list.body().text, decompressed_limit);

    EXPECT_EQ(texts(reread.data()), texts(list.data()));
    EXPECT_EQ(texts(reread.search()), texts(list.search()));
}

// A list whose JSON would not fit is sent compressed, whole where it fits.
TEST(SeekListTest, CompressedWhereJsonWouldNotFit) {
    constexpr std::size_t count = 100;
    constexpr std::size_t limit = 1'000;
    // The same identifier, many times over, compresses to little.
    const SeekList list(std::vector<Identifier>(count, Identifier("sha256", hello_hash)), {});

    const auto body = list.body(limit);
    const auto reread = SeekList::from_body(body.text, decompressed_limit);

    EXPECT_LT(body.text.size(), limit);
    EXPECT_TRUE(body.compressed);
    EXPECT_TRUE(Zlib::decompress(Converter::bytes_of(body.text), decompressed_limit).has_value());
    EXPECT_EQ(texts(reread.data()), texts(list.data()));
}

// What does not fit even compressed is left out from the end, keeping as
// many of each kind as of the other, so that neither crowds the other out,
// and as many as fit: one more of each would not.
TEST(SeekListTest, WhatDoesNotFitIsLeftOutFromTheEnd) {
    constexpr std::size_t count = 100;
    constexpr std::size_t limit = 2'000;
    const SeekList list(identifiers(count), prefixes(count));

    const auto body = list.body(limit);
    const auto reread = SeekList::from_body(body.text, decompressed_limit);
    const auto kept = reread.data().size();
    const SeekList one_more(first(list.data(), kept + 1), first(list.search(), kept + 1));

    EXPECT_LT(body.text.size(), limit);
    EXPECT_GT(kept, 0U);
    EXPECT_LT(kept, count);
    EXPECT_EQ(texts(reread.data()), texts(first(list.data(), kept)));
    EXPECT_EQ(texts(reread.search()), texts(first(list.search(), kept)));
    EXPECT_EQ(SeekList::from_body(one_more.body(limit).text, decompressed_limit).data().size(),
              kept);
}

// A kind with fewer than fit is kept whole, and the other has the room it
// leaves.
TEST(SeekListTest, KindWithFewerIsKeptWhole) {
    constexpr std::size_t count = 100;
    constexpr std::size_t fewer = 2;
    constexpr std::size_t limit = 2'000;
    const SeekList list(identifiers(count), prefixes(fewer));

    const auto reread = SeekList::from_body(list.body(limit).text, decompressed_limit);

    EXPECT_GT(reread.data().size(), fewer);
    EXPECT_LT(reread.data().size(), count);
    EXPECT_EQ(texts(reread.search()), texts(list.search()));
}

// {"data":[],"search":[]} is 23 bytes, so a limit of 23 holds no list.
TEST(SeekListTest, LimitTooSmallForAnEmptyListIsRefused) {
    constexpr std::size_t empty_list_size = 23;
    const SeekList list(identifiers(1), prefixes(1));

    EXPECT_THROW((void)list.body(empty_list_size), std::invalid_argument);
    EXPECT_EQ(list.body(empty_list_size + 1).text, R"({"data":[],"search":[]})");
}

// cppcheck-suppress-end danglingTemporaryLifetime

// A list is sent in less than 1 MiB unless told otherwise. One of 20,000
// identifiers is about 1.4 MiB as JSON, and half that compressed.
TEST(SeekListTest, WrittenInLessThanOneMebibyte) {
    constexpr std::size_t count = 20'000;
    const SeekList list(identifiers(count), {});

    const auto body = list.body();
    const auto reread = SeekList::from_body(body.text, decompressed_limit);

    EXPECT_EQ(SeekList::max_size, 1'048'576U);
    EXPECT_LT(body.text.size(), SeekList::max_size);
    EXPECT_TRUE(body.compressed);
    EXPECT_TRUE(Zlib::decompress(Converter::bytes_of(body.text), decompressed_limit).has_value());
    EXPECT_EQ(texts(reread.data()), texts(list.data()));
}
