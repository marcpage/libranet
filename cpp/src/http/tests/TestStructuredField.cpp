#include <gtest/gtest.h>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "http/StructuredField.hpp"

using http::StructuredField;

namespace {

using Bytes = StructuredField::Bytes;
using InnerList = StructuredField::InnerList;
using Item = StructuredField::Item;
using Member = StructuredField::Member;
using Parameters = StructuredField::Parameters;
using Token = StructuredField::Token;

// Whether StructuredField::parse() turns down text as invalid.
bool refused(std::string_view text) {
    try {
        (void)StructuredField::parse(text);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

// Whether StructuredField::set() turns down key naming value.
bool refused_to_set(std::string key, Member value) {
    try {
        StructuredField field;

        field.set(std::move(key), std::move(value));
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

// What StructuredField::parse() reads from text, written back out.
std::string rewritten(std::string_view text) {
    return StructuredField::parse(text).to_string();
}

// The bytes of text.
Bytes bytes_of(std::string_view text) {
    Bytes bytes(text.size());

    std::transform(text.begin(), text.end(), bytes.begin(),
                   [](char character) { return static_cast<std::byte>(character); });
    return bytes;
}

// count keys, from k0 on, each after separator.
std::string keys(std::size_t count, std::string_view separator) {
    std::string text;

    for (std::size_t index = 0; index < count; ++index) {
        text += separator;
        text += "k" + std::to_string(index);
    }

    return text;
}

// count parameters, from k0 on, each true.
Parameters parameters(std::size_t count) {
    Parameters result;

    for (std::size_t index = 0; index < count; ++index) {
        result.emplace_back("k" + std::to_string(index), true);
    }

    return result;
}

// A string item, with no parameters, as an inner list of a Signature-Input
// names a component.
Item component(std::string name) {
    return Item{std::move(name), {}};
}

} // namespace

// RFC 9651 §3.2's example.
TEST(StructuredFieldTest, ReadsADictionary) {
    // "Æbletærte", in UTF-8.
    constexpr std::string_view aebletaerte = "\xC3\x86\x62let\xC3\xA6rte";
    const auto field = StructuredField::parse(R"(en="Applepie", da=:w4ZibGV0w6ZydGU=:)");

    ASSERT_EQ(field.members().size(), 2U);
    EXPECT_EQ(field.members().at(0).first, "en");
    EXPECT_EQ(field.members().at(1).first, "da");
    EXPECT_EQ(field.member("en"), Member(Item{std::string("Applepie"), {}}));
    EXPECT_EQ(field.member("da"), Member(Item{bytes_of(aebletaerte), {}}));
    EXPECT_EQ(field.to_string(), R"(en="Applepie", da=:w4ZibGV0w6ZydGU=:)");
}

// RFC 9651 §3.2's example.
TEST(StructuredFieldTest, AKeyOrParameterAloneIsTrue) {
    const auto field = StructuredField::parse("a=?0, b, c; foo=bar");

    EXPECT_EQ(field.member("a"), Member(Item{false, {}}));
    EXPECT_EQ(field.member("b"), Member(Item{true, {}}));
    EXPECT_EQ(field.member("c"), Member(Item{true, {{"foo", Token{"bar"}}}}));
    EXPECT_EQ(field.to_string(), "a=?0, b, c;foo=bar");
}

// RFC 9651 §3.2's example.
TEST(StructuredFieldTest, ReadsInnerListsAndParameters) {
    const auto field = StructuredField::parse("a=(1 2), b=3, c=4;aa=bb, d=(5 6);valid");
    const Item five{std::int64_t{5}, {}};
    const Item six{std::int64_t{6}, {}};

    EXPECT_EQ(field.member("c"), Member(Item{std::int64_t{4}, {{"aa", Token{"bb"}}}}));
    EXPECT_EQ(field.member("d"), Member(InnerList{{five, six}, {{"valid", true}}}));
    EXPECT_EQ(field.to_string(), "a=(1 2), b=3, c=4;aa=bb, d=(5 6);valid");
}

// RFC 9421 §4.1's example.
TEST(StructuredFieldTest, ReadsASignatureInput) {
    const std::string text = R"(sig1=("@method" "@authority" "@path" "content-digest")"
                             R"( "content-length" "content-type"))"
                             R"(;created=1618884473;keyid="test-key-rsa-pss")";

    const auto field = StructuredField::parse(text);
    const auto& list = std::get<InnerList>(field.members().at(0).second);

    EXPECT_EQ(field.members().at(0).first, "sig1");
    ASSERT_EQ(list.items.size(), 6U);
    EXPECT_EQ(list.items.at(0), component("@method"));
    EXPECT_EQ(list.items.at(5), component("content-type"));
    EXPECT_EQ(list.parameters, (Parameters{{"created", std::int64_t{1618884473}},
                                           {"keyid", std::string("test-key-rsa-pss")}}));
    EXPECT_EQ(field.to_string(), text);
}

// As HTTP API §11 has a node sign a request with a body.
TEST(StructuredFieldTest, WritesASignatureInput) {
    constexpr std::int64_t created = 1757080000;
    const InnerList signature_input{
        {component("@method"), component("@path"), component("content-digest")},
        {{"created", created}, {"keyid", std::string("sha256/def567")}},
    };
    StructuredField field;

    field.set("libranet", signature_input);

    EXPECT_EQ(field.to_string(), R"(libranet=("@method" "@path" "content-digest"))"
                                 R"(;created=1757080000;keyid="sha256/def567")");
}

// RFC 9530 §2's example, the SHA-256 of {"hello": "world"}.
TEST(StructuredFieldTest, ReadsAContentDigest) {
    constexpr std::string_view text = "sha-256=:X48E9qOokqqrvdts8nOJRJN3OWDUoyWxBf7kbu9DBPE=:";

    const auto field = StructuredField::parse(text);
    const auto& digest = std::get<Item>(field.members().at(0).second);

    EXPECT_EQ(field.members().at(0).first, "sha-256");
    ASSERT_EQ(std::get<Bytes>(digest.value).size(), 32U);
    EXPECT_EQ(std::get<Bytes>(digest.value).at(0), std::byte{0x5F});
    EXPECT_EQ(std::get<Bytes>(digest.value).at(31), std::byte{0xF1});
    EXPECT_EQ(field.to_string(), text);
}

// RFC 9651 §3.3.5's example.
TEST(StructuredFieldTest, ReadsAByteSequence) {
    const auto field = StructuredField::parse("a=:cHJldGVuZCB0aGlzIGlzIGJpbmFyeSBjb250ZW50Lg==:");

    EXPECT_EQ(field.member("a"), Member(Item{bytes_of("pretend this is binary content."), {}}));
}

// RFC 9651 §4.2.7 asks that a byte sequence be read without its padding.
TEST(StructuredFieldTest, ReadsAByteSequenceWithoutPadding) {
    EXPECT_EQ(StructuredField::parse("a=:AQI:").member("a"),
              Member(Item{bytes_of("\x01\x02"), {}}));
    EXPECT_EQ(rewritten("a=:AQI:"), "a=:AQI=:");
    EXPECT_EQ(rewritten("a=::"), "a=::");
}

TEST(StructuredFieldTest, ReadsStringsWithTheirEscapes) {
    const auto field = StructuredField::parse(R"(a="say \"hi\" \\ bye", b="")");

    EXPECT_EQ(field.member("a"), Member(Item{std::string(R"(say "hi" \ bye)"), {}}));
    EXPECT_EQ(field.member("b"), Member(Item{std::string(), {}}));
    EXPECT_EQ(field.to_string(), R"(a="say \"hi\" \\ bye", b="")");
}

// RFC 9651 §3.3.4's example, and each character a token may hold.
TEST(StructuredFieldTest, ReadsTokens) {
    EXPECT_EQ(StructuredField::parse("a=foo123/456").member("a"),
              Member(Item{Token{"foo123/456"}, {}}));
    EXPECT_EQ(rewritten("a=*Az09!#$%&'*+-.^_`|~:/"), "a=*Az09!#$%&'*+-.^_`|~:/");
}

TEST(StructuredFieldTest, IntegersHaveUpToFifteenDigits) {
    EXPECT_EQ(rewritten("a=999999999999999, b=-999999999999999"),
              "a=999999999999999, b=-999999999999999");
    EXPECT_TRUE(refused("a=1000000000000000"));
    EXPECT_TRUE(refused("a=-1000000000000000"));
    EXPECT_TRUE(refused("a=0000000000000001"));
}

TEST(StructuredFieldTest, WritesWhatItReadsInItsOwnForm) {
    EXPECT_EQ(rewritten("  a=?1,b=-0 ,\tc=007;p=?1;q=?0 \t"), "a, b=0, c=7;p;q=?0");
    EXPECT_EQ(rewritten("a=( 1  2 );x;y=?1"), "a=(1 2);x;y");
    EXPECT_EQ(rewritten("a=();x"), "a=();x");
}

TEST(StructuredFieldTest, AKeyGivenTwiceKeepsItsFirstPlaceAndLastValue) {
    EXPECT_EQ(rewritten("a=1, b=2, a=3;x=1;y;x=2"), "a=3;x=2;y, b=2");
}

TEST(StructuredFieldTest, EmptyTextHasNoMembers) {
    EXPECT_TRUE(StructuredField::parse("").members().empty());
    EXPECT_TRUE(StructuredField::parse("   ").members().empty());
    EXPECT_EQ(StructuredField().to_string(), "");
}

TEST(StructuredFieldTest, AMissingKeyNamesNothing) {
    EXPECT_FALSE(StructuredField::parse("a=1").member("b").has_value());
}

TEST(StructuredFieldTest, SettingAKeyAgainReplacesItsMember) {
    auto field = StructuredField::parse("a=1, b=2");

    field.set("a", Item{Token{"three"}, {}});
    field.set("c", Item{Bytes{}, {}});

    EXPECT_EQ(field.to_string(), "a=three, b=2, c=::");
}

TEST(StructuredFieldTest, ComparesMembersInOrder) {
    const auto field = StructuredField::parse("a=1, b=2");

    EXPECT_EQ(field, StructuredField::parse("a=1,b=2"));
    EXPECT_NE(field, StructuredField::parse("b=2, a=1"));
    EXPECT_NE(field, StructuredField::parse("a=1;x, b=2"));
    EXPECT_NE(field.member("a"), Member(Item{Token{"a"}, {}}));
    EXPECT_NE(Member(InnerList{{}, {}}), Member(InnerList{{}, {{"x", true}}}));
    EXPECT_NE(Token{"a"}, Token{"b"});
}

TEST(StructuredFieldTest, TextThatIsNotADictionaryIsRefused) {
    EXPECT_TRUE(refused("A=1"));
    EXPECT_TRUE(refused("1=1"));
    EXPECT_TRUE(refused("\ta=1"));
    EXPECT_TRUE(refused(",a=1"));
    EXPECT_TRUE(refused("a=1,"));
    EXPECT_TRUE(refused("a=1, "));
    EXPECT_TRUE(refused("a=1,,b=2"));
    EXPECT_TRUE(refused("a=1 b=2"));
    EXPECT_TRUE(refused("a="));
    EXPECT_TRUE(refused("a=1;"));
    EXPECT_TRUE(refused("a=1;B=2"));
    EXPECT_TRUE(refused("a=\xC3\xA9"));
    EXPECT_TRUE(refused(std::string("a=1\0", 4)));
}

TEST(StructuredFieldTest, ItemsThatCannotBeReadAreRefused) {
    EXPECT_TRUE(refused("a=-"));
    EXPECT_TRUE(refused("a=--1"));
    EXPECT_TRUE(refused("a=1.5"));
    EXPECT_TRUE(refused(R"(a="open)"));
    EXPECT_TRUE(refused(R"(a="\)"));
    EXPECT_TRUE(refused(R"(a="\q")"));
    EXPECT_TRUE(refused("a=\"tab\t\""));
    EXPECT_TRUE(refused("a=\"\xC3\xA9\""));
    EXPECT_TRUE(refused("a=:AQI"));
    EXPECT_TRUE(refused("a=:A$I=:"));
    EXPECT_TRUE(refused("a=:A===:"));
    EXPECT_TRUE(refused("a=:AA=A:"));
    EXPECT_TRUE(refused("a=:AA==AA==:"));
    EXPECT_TRUE(refused("a=?"));
    EXPECT_TRUE(refused("a=?2"));
    EXPECT_TRUE(refused("a=@1659578233"));
    EXPECT_TRUE(refused(R"(a=%"f%c3%bc%c3%bc")"));
    EXPECT_TRUE(refused("a=(1 2"));
    EXPECT_TRUE(refused("a=(1,2)"));
    EXPECT_TRUE(refused("a=((1))"));
}

// RFC 9651 asks a parser to take at least 1024 members, 256 items in an
// inner list, and 256 parameters (§3.2, §3.1.1, §3.1.2).
TEST(StructuredFieldTest, HoldsWhatRfc9651AsksAParserToTake) {
    constexpr std::size_t members = 1024;
    constexpr std::size_t items = 256;
    constexpr std::size_t parameter_count = 256;

    EXPECT_EQ(StructuredField::parse(keys(members, ",").substr(1)).members().size(), members);
    EXPECT_TRUE(refused(keys(members + 1, ",").substr(1)));
    EXPECT_FALSE(refused("a=(" + keys(items, " ") + ")"));
    EXPECT_TRUE(refused("a=(" + keys(items + 1, " ") + ")"));
    EXPECT_FALSE(refused("a" + keys(parameter_count, ";")));
    EXPECT_TRUE(refused("a" + keys(parameter_count + 1, ";")));
    EXPECT_FALSE(refused_to_set("a", Item{true, parameters(parameter_count)}));
    EXPECT_TRUE(refused_to_set("a", Item{true, parameters(parameter_count + 1)}));
}

TEST(StructuredFieldTest, KeysThatCannotBeWrittenAreRefused) {
    EXPECT_TRUE(refused_to_set("", Item{true, {}}));
    EXPECT_TRUE(refused_to_set("A", Item{true, {}}));
    EXPECT_TRUE(refused_to_set("1a", Item{true, {}}));
    EXPECT_TRUE(refused_to_set("a b", Item{true, {}}));
    EXPECT_TRUE(refused_to_set("a", Item{true, {{"B", true}}}));
    EXPECT_TRUE(refused_to_set("a", Item{true, {{"b", true}, {"b", false}}}));
    EXPECT_TRUE(refused_to_set("a", InnerList{{}, {{"b", true}, {"b", false}}}));
}

TEST(StructuredFieldTest, ValuesThatCannotBeWrittenAreRefused) {
    constexpr std::int64_t too_large = 1'000'000'000'000'000;

    EXPECT_TRUE(refused_to_set("a", Item{too_large, {}}));
    EXPECT_TRUE(refused_to_set("a", Item{-too_large, {}}));
    EXPECT_TRUE(refused_to_set("a", Item{std::string("line\n"), {}}));
    EXPECT_TRUE(refused_to_set("a", Item{std::string("\xC3\xA9"), {}}));
    EXPECT_TRUE(refused_to_set("a", Item{Token{""}, {}}));
    EXPECT_TRUE(refused_to_set("a", Item{Token{"1a"}, {}}));
    EXPECT_TRUE(refused_to_set("a", Item{Token{"a b"}, {}}));
    EXPECT_TRUE(refused_to_set("a", Item{true, {{"b", Token{"a b"}}}}));
    EXPECT_TRUE(refused_to_set("a", InnerList{{Item{Token{"a b"}, {}}}, {}}));
}
