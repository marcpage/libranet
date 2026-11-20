#include <gtest/gtest.h>
#include <algorithm>
#include <cstddef>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "protocol/SearchResults.hpp"
#include "storage/Identifier.hpp"

using protocol::SearchResults;
using storage::Identifier;

namespace {

// The hash of "hello", as `shasum -a 256` gives it, and its identifier and
// that of "abc".
constexpr auto hello_hash = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";
constexpr auto hello = "sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";
constexpr auto abc = "sha256/ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

// The answer naming both, as the Python node writes it, with
// compact_json({"results": [hello, abc]}).
constexpr std::string_view python_answer =
    R"({"results":["sha256/2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",)"
    R"("sha256/ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"]})";

// The same answer, zlib-compressed, in hex, as Python's
// zlib.compress(python_answer, 6) writes it.
constexpr std::string_view python_compressed_answer =
    "789c35cc3d0e82210c00d0bb309bd81f5ac0ab188716da3838894ec6bbfb2de6edef539eb1df8fd72e976bd9"
    "7723d133cda4badc241d8c2148a3b3934df11134021d154326a6b54a128da1322b0dee3e3ad572fa4f6eada37"
    "af6049c1956f1002b645910113b002b1a0fc5d66c4caf08998a490028b6caedfb0395752c9e";

// "anything", which is not JSON, and "{}", which is no answer, each as
// Python's zlib.compress(text, 6) writes it, in hex.
constexpr std::string_view python_compressed_anything = "789c4bccab2cc9c8cc4b07000f4a0363";
constexpr std::string_view python_compressed_object = "789cabae0500017500f9";

// Enough for a test's answer to decompress to.
constexpr std::size_t enough = 1024;

// HttpApi §6.1: an answer is less than 1 MiB as sent.
constexpr std::size_t max_body_size = 1024UL * 1024UL;

// The text that lowercase hex names, a char for each byte.
std::string text_of_hex(std::string_view hex) {
    constexpr std::string_view digits = "0123456789abcdef";
    constexpr auto nibble_bits = 4U;
    std::string text;

    for (std::size_t index = 0; index + 1 < hex.size(); index += 2) {
        const auto high = digits.find(hex.at(index));
        const auto low = digits.find(hex.at(index + 1));

        text.push_back(static_cast<char>((high << nibble_bits) | low));
    }

    return text;
}

// text as a JSON string.
std::string json_string(std::string_view text) {
    return "\"" + std::string(text) + "\"";
}

// The answer whose "results" are entries, each written as JSON.
std::string answer_of(const std::vector<std::string>& entries) {
    std::string body = R"({"results":[)";

    for (std::size_t index = 0; index < entries.size(); ++index) {
        body += (0 == index ? "" : ",") + entries.at(index);
    }

    return body + "]}";
}

// count identifiers, each of the content of a number, from 0, so that no two
// are alike.
SearchResults::Identifiers numbered(std::size_t count) {
    SearchResults::Identifiers identifiers;

    identifiers.reserve(count);

    for (std::size_t number = 0; number < count; ++number) {
        Identifier::Data data;

        for (const auto character : std::to_string(number)) {
            data.push_back(static_cast<std::byte>(character));
        }

        identifiers.push_back(Identifier::from(data));
    }

    return identifiers;
}

// The first count of identifiers.
SearchResults::Identifiers first(const SearchResults::Identifiers& identifiers,
                                 std::size_t count) {
    const auto end = std::next(identifiers.begin(), static_cast<std::ptrdiff_t>(count));

    return {identifiers.begin(), end};
}

// What identifiers name, as text.
std::vector<std::string> texts(const SearchResults::Identifiers& identifiers) {
    std::vector<std::string> names;

    names.reserve(identifiers.size());
    std::transform(identifiers.begin(), identifiers.end(), std::back_inserter(names),
                   [](const Identifier& identifier) { return identifier.to_string(); });
    return names;
}

// What the answer body names, as text, when it decompresses to no more than
// limit bytes.
std::vector<std::string> named(std::string_view body, std::size_t limit = 4 * max_body_size) {
    return texts(SearchResults::parse(body, limit).results());
}

// Whether SearchResults::parse() turns down body as no answer.
bool refused(std::string_view body, std::size_t limit = enough) {
    try {
        (void)SearchResults::parse(body, limit);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

} // namespace

TEST(SearchResultsTest, ReadsWhatPythonWrote) {
    EXPECT_EQ(named(python_answer), (std::vector<std::string>{hello, abc}));
}

TEST(SearchResultsTest, ReadsWhatPythonCompressed) {
    const auto body = text_of_hex(python_compressed_answer);

    EXPECT_EQ(named(body), (std::vector<std::string>{hello, abc}));
}

TEST(SearchResultsTest, WritesAsPythonDoes) {
    const SearchResults answer({Identifier::from(hello), Identifier::from(abc)});

    const auto body = answer.body();

    EXPECT_EQ(body.text, python_answer);
    EXPECT_FALSE(body.compressed);
}

TEST(SearchResultsTest, NothingFound) {
    const SearchResults answer({});

    const auto body = answer.body();

    EXPECT_EQ(body.text, R"({"results":[]})");
    EXPECT_FALSE(body.compressed);
    EXPECT_TRUE(named(body.text).empty());
}

TEST(SearchResultsTest, ReadAndWrittenAgainAlike) {
    const auto read = SearchResults::parse(text_of_hex(python_compressed_answer), enough);

    EXPECT_EQ(read.body().text, python_answer);
}

TEST(SearchResultsTest, HashesAreKeptInLowerCase) {
    constexpr auto upper_case =
        "sha256/2CF24DBA5FB0A30E26E83B2AC5B9E29E1B161E5C1FA7425E73043362938B9824";
    const auto body = answer_of({json_string(upper_case)});

    EXPECT_EQ(named(body), (std::vector<std::string>{hello}));
}

TEST(SearchResultsTest, EntriesThatAreNotIdentifiersAreLeftOut) {
    const std::string hash(hello_hash);
    const auto body = answer_of({
        "7",
        "null",
        "true",
        "{}",
        "[]",
        json_string(""),
        json_string(abc),
        json_string("sha512/" + hash),
        json_string("sha256/" + hash.substr(1)),
        json_string("sha256/" + hash.substr(1) + "g"),
        json_string("sha256" + hash),
        json_string(hash),
        json_string("/" + std::string(hello)),
        json_string(std::string(hello) + "/more"),
        json_string(hello),
    });

    EXPECT_EQ(named(body), (std::vector<std::string>{abc, hello}));
}

TEST(SearchResultsTest, BodyThatIsNoAnswerIsRefused) {
    EXPECT_TRUE(refused(""));
    EXPECT_TRUE(refused("results"));
    EXPECT_TRUE(refused("null"));
    EXPECT_TRUE(refused("[" + json_string(hello) + "]"));
    EXPECT_TRUE(refused("{}"));
    EXPECT_TRUE(refused(R"({"result":[]})"));
    EXPECT_TRUE(refused(R"({"results":{}})"));
    EXPECT_TRUE(refused(R"({"results":)" + json_string(hello) + "}"));
    EXPECT_TRUE(refused(R"({"results":[])"));
}

TEST(SearchResultsTest, CompressedBodyThatIsNoAnswerIsRefused) {
    const auto compressed = text_of_hex(python_compressed_answer);

    EXPECT_TRUE(refused(compressed.substr(0, compressed.size() - 1)));
    EXPECT_TRUE(refused(compressed + "x"));
    EXPECT_TRUE(refused(text_of_hex(python_compressed_anything)));
    EXPECT_TRUE(refused(text_of_hex(python_compressed_object)));
}

TEST(SearchResultsTest, BodyThatDecompressesPastTheLimitIsRefused) {
    const auto compressed = text_of_hex(python_compressed_answer);

    EXPECT_EQ(named(compressed, python_answer.size()), (std::vector<std::string>{hello, abc}));
    EXPECT_TRUE(refused(compressed, python_answer.size() - 1));
}

// Each identifier takes 74 bytes of compact JSON, its comma among them, and
// the rest of the answer 13 more: 14,169 come to 1,048,519 bytes, under
// 1 MiB, and 14,170 to 1,048,593, over it.
TEST(SearchResultsTest, OnlyAnAnswerThatWouldNotFitIsCompressed) {
    constexpr std::size_t most_uncompressed = 14'169;
    const auto found = numbered(most_uncompressed + 1);

    const auto fitting = SearchResults(first(found, most_uncompressed)).body();
    const auto compressed = SearchResults(found).body();

    EXPECT_FALSE(fitting.compressed);
    EXPECT_EQ(fitting.text.size(), 1'048'519U);
    EXPECT_EQ(named(fitting.text), texts(first(found, most_uncompressed)));
    EXPECT_TRUE(compressed.compressed);
    EXPECT_LT(compressed.text.size(), max_body_size);
    EXPECT_EQ(named(compressed.text), texts(found));
}

// Compressed, an identifier takes about half as much, so 30,000 do not fit.
TEST(SearchResultsTest, AsManyAsFitAreNamed) {
    constexpr std::size_t count = 30'000;
    const auto found = numbered(count);

    const auto body = SearchResults(found).body();
    const auto names = named(body.text);
    const auto one_more = SearchResults(first(found, names.size() + 1)).body();

    EXPECT_TRUE(body.compressed);
    EXPECT_LT(body.text.size(), max_body_size);
    EXPECT_LT(names.size(), count);
    EXPECT_EQ(names, texts(first(found, names.size())));
    EXPECT_EQ(one_more.text, body.text);
}

TEST(SearchResultsTest, AnswersAResponse) {
    constexpr auto status = http::ResponseCode::Status::Accepted;
    const SearchResults answer({Identifier::from(hello), Identifier::from(abc)});
    http::Response response;

    response.set_status_code(status);
    response.headers().set("X-Request-Path", "/data/search/2cf2");
    answer.answer(response);

    EXPECT_EQ(response.code().status(), status);
    EXPECT_EQ(response.headers().get("Content-Type"), "application/json");
    EXPECT_FALSE(response.headers().has("Content-Encoding"));
    EXPECT_EQ(response.headers().get("X-Request-Path"), "/data/search/2cf2");
    EXPECT_EQ(response.body(), python_answer);
}

TEST(SearchResultsTest, AnswerTooLargeAsItIsSaysItIsCompressed) {
    constexpr std::size_t count = 20'000;
    const auto found = numbered(count);
    http::Response response;

    SearchResults(found).answer(response);

    EXPECT_EQ(response.headers().get("Content-Type"), "application/json");
    EXPECT_EQ(response.headers().get("Content-Encoding"), "deflate");
    EXPECT_LT(response.body().size(), max_body_size);
    EXPECT_EQ(named(response.body()), texts(found));
}
