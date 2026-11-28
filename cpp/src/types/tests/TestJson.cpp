#include <gtest/gtest.h>
#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>

#include "types/Json.hpp"

using types::Json;

namespace {

// Whether Json::parse turns down text as invalid.
bool refused(const std::string& text) {
    try {
        (void)Json::parse(text);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

} // namespace

TEST(JsonTest, ConstructorsAndTypes) {
    // Default constructor (null state)
    const Json null_json;
    EXPECT_TRUE(null_json.is_null());
    EXPECT_FALSE(null_json.is_boolean());
    EXPECT_FALSE(null_json.is_number());
    EXPECT_FALSE(null_json.is_string());
    EXPECT_FALSE(null_json.is_array());
    EXPECT_FALSE(null_json.is_object());

    // Explicit nullptr constructor
    const Json explicit_null(nullptr);
    EXPECT_TRUE(explicit_null.is_null());

    // Boolean constructors
    const Json true_json(true);
    const Json false_json(false);
    EXPECT_TRUE(true_json.is_boolean());
    EXPECT_TRUE(true_json.boolean());
    EXPECT_FALSE(false_json.boolean());

    // Numeric constructors (integers and floating point)
    constexpr auto int_value = 42;
    constexpr int64_t int64_value = -9'223'372'036'854'775'807LL;
    constexpr auto double_value = 3.14159;
    const Json int_json(int_value);
    const Json int64_json(int64_value);
    const Json double_json(double_value);

    EXPECT_TRUE(int_json.is_number());
    EXPECT_TRUE(int64_json.is_number());
    EXPECT_TRUE(double_json.is_number());
    EXPECT_EQ(int_json.integer(), int_value);
    EXPECT_DOUBLE_EQ(double_json.real(), double_value);

    // String constructors (const char* and std::string)
    const Json string_json("Hello, World!");
    const std::string text = "C++17 JSON";
    const Json std_string_json(text);

    EXPECT_TRUE(string_json.is_string());
    EXPECT_TRUE(std_string_json.is_string());
    EXPECT_EQ(string_json.string(), "Hello, World!");
    EXPECT_EQ(std_string_json.string(), "C++17 JSON");
}

TEST(JsonTest, TypeOfEachValue) {
    constexpr auto int_value = 42;
    constexpr auto double_value = 3.14159;

    EXPECT_EQ(Json().type(), Json::Type::Null);
    EXPECT_EQ(Json(nullptr).type(), Json::Type::Null);
    EXPECT_EQ(Json(true).type(), Json::Type::Boolean);
    EXPECT_EQ(Json(int_value).type(), Json::Type::Integer);
    EXPECT_EQ(Json(double_value).type(), Json::Type::Real);
    EXPECT_EQ(Json("text").type(), Json::Type::String);
    EXPECT_EQ(Json(Json::Array{}).type(), Json::Type::Array);
    EXPECT_EQ(Json(Json::Object{}).type(), Json::Type::Object);
}

TEST(JsonTest, InitializerLists) {
    constexpr auto real_value = 3.0;
    Json array_json = {1, "two", real_value, true, nullptr};
    EXPECT_TRUE(array_json.is_array());
    EXPECT_EQ(array_json.size(), 5U);
    EXPECT_EQ(array_json[0].integer(), 1);
    EXPECT_EQ(array_json[1].string(), "two");
    EXPECT_DOUBLE_EQ(array_json[2].real(), real_value);
    EXPECT_TRUE(array_json[3].boolean());
    EXPECT_TRUE(array_json[4].is_null());

    constexpr auto age = 30;
    constexpr auto first_score = 95;
    constexpr auto second_score = 88;
    constexpr auto third_score = 100;
    Json object_json = {
        {"name", "Alice"},
        {"age", age},
        {"is_admin", false},
        {"scores", {first_score, second_score, third_score}},
    };

    EXPECT_TRUE(object_json.is_object());
    EXPECT_EQ(object_json.size(), 4U);
    EXPECT_EQ(object_json["name"].string(), "Alice");
    EXPECT_EQ(object_json["age"].integer(), age);
    EXPECT_FALSE(object_json["is_admin"].boolean());
    EXPECT_TRUE(object_json["scores"].is_array());
    EXPECT_EQ(object_json["scores"].size(), 3U);
}

TEST(JsonTest, SubscriptAndAccess) {
    // Object access and implicit object creation
    constexpr auto level = 5;
    Json doc;
    doc["user"] = "Bob";
    doc["active"] = true;
    doc["details"] = {{"role", "developer"}, {"level", level}};

    EXPECT_TRUE(doc.is_object());
    EXPECT_EQ(doc["user"].string(), "Bob");
    EXPECT_EQ(doc["details"]["role"].string(), "developer");
    EXPECT_EQ(doc["details"]["level"].integer(), level);

    // Array indexing
    constexpr auto first_value = 10;
    constexpr auto second_value = 20;
    constexpr auto third_value = 30;
    Json array_json = Json::Array({first_value, second_value, third_value});
    EXPECT_TRUE(array_json.is_array());
    EXPECT_EQ(array_json[0].integer(), first_value);
    EXPECT_EQ(array_json[1].integer(), second_value);
    EXPECT_EQ(array_json[2].integer(), third_value);

    // Mutating elements via indexing
    constexpr auto replacement_value = 25;
    array_json[1] = replacement_value;
    EXPECT_EQ(array_json[1].integer(), replacement_value);

    doc["user"] = "Charlie";
    EXPECT_EQ(doc["user"].string(), "Charlie");
}

// This test's try and catch blocks, and gtest's assertion macros, which count
// as branches, put it over this check's limit.
// NOLINTBEGIN(readability-function-cognitive-complexity)
TEST(JsonTest, ExceptionsAndTypeSafety) {
    constexpr auto number = 100;
    const Json number_json(number);

    // Expect std::domain_error or std::runtime_error when requesting bad
    // conversions
    bool caught_bad_string_cast = false;

    try {
        (void)number_json.string();
    } catch (const std::domain_error&) {
        caught_bad_string_cast = true;
    } catch (const std::runtime_error&) {
        caught_bad_string_cast = true;
    }

    EXPECT_TRUE(caught_bad_string_cast);

    bool caught_bad_bool_cast = false;

    try {
        (void)number_json.boolean();
    } catch (const std::domain_error&) {
        caught_bad_bool_cast = true;
    } catch (const std::runtime_error&) {
        caught_bad_bool_cast = true;
    }

    EXPECT_TRUE(caught_bad_bool_cast);

    // Out-of-bounds array access check
    Json array_json = {1, 2, 3};
    constexpr std::size_t out_of_range_index = 10;
    EXPECT_THROW((void)array_json.at(out_of_range_index), std::out_of_range);
}
// NOLINTEND(readability-function-cognitive-complexity)

TEST(JsonTest, SerializationAndStreams) {
    constexpr auto user_id = 1'001;
    const Json user = {
        {"id", user_id},
        {"active", true},
        {"tags", {"cpp", "json"}},
    };

    // Serialized output test
    const std::string json_text = user.dump();
    EXPECT_FALSE(json_text.empty());

    // Stream operator test
    std::ostringstream out_stream;
    out_stream << user;
    EXPECT_EQ(out_stream.str(), json_text);
}

TEST(JsonTest, NestedComplexStructures) {
    Json root = {
        {"company", "TechCorp"},
        {
            "employees",
            {
                {{"id", 1}, {"name", "Alice"}, {"skills", {"C++", "Python"}}},
                {{"id", 2}, {"name", "Bob"}, {"skills", {"Rust", "Go"}}},
            },
        },
    };

    EXPECT_TRUE(root["employees"].is_array());
    EXPECT_EQ(root["employees"].size(), 2U);
    EXPECT_EQ(root["employees"][0]["name"].string(), "Alice");
    EXPECT_EQ(root["employees"][1]["skills"][0].string(), "Rust");
}

TEST(JsonTest, ParseValues) {
    // Each kind of value parses to its own type and writes out as it was read.
    EXPECT_TRUE(Json::parse("null").is_null());
    EXPECT_TRUE(Json::parse("true").is_boolean());
    EXPECT_TRUE(Json::parse("false").is_boolean());
    EXPECT_TRUE(Json::parse("-12").is_integer());
    EXPECT_TRUE(Json::parse("2.5").is_real());
    EXPECT_TRUE(Json::parse("\"text\"").is_string());
    EXPECT_TRUE(Json::parse("[1,2]").is_array());
    EXPECT_TRUE(Json::parse(R"({"key":"value"})").is_object());

    EXPECT_EQ(Json::parse("null").dump(), "null");
    EXPECT_EQ(Json::parse("true").dump(), "true");
    EXPECT_EQ(Json::parse("false").dump(), "false");
    EXPECT_EQ(Json::parse("-12").dump(), "-12");
    EXPECT_EQ(Json::parse("2.5").dump(), "2.5");
    EXPECT_EQ(Json::parse("\"text\"").dump(), "\"text\"");
    EXPECT_EQ(Json::parse("[1,2]").dump(), "[1,2]");
    EXPECT_EQ(Json::parse(R"({"key":"value"})").dump(), R"({"key":"value"})");
}

TEST(JsonTest, NumbersOutOfRange) {
    // Too large for a 64-bit integer, and too large for a double.
    EXPECT_THROW((void)Json::parse("99999999999999999999"), std::invalid_argument);
    EXPECT_THROW((void)Json::parse("1e999"), std::invalid_argument);
}

TEST(JsonTest, NestingLimit) {
    constexpr std::size_t deepest = 128;

    const std::string deepest_arrays = std::string(deepest, '[') + std::string(deepest, ']');
    EXPECT_EQ(Json::parse(deepest_arrays).dump().size(), 2 * deepest);

    const std::string deeper_arrays =
        std::string(deepest + 1, '[') + std::string(deepest + 1, ']');
    EXPECT_THROW((void)Json::parse(deeper_arrays), std::invalid_argument);

    // Objects count as levels too.
    std::string deeper_objects;

    for (std::size_t level = 0; level <= deepest; ++level) {
        deeper_objects += R"({"a":)";
    }

    deeper_objects += "null" + std::string(deepest + 1, '}');
    EXPECT_THROW((void)Json::parse(deeper_objects), std::invalid_argument);
}

TEST(JsonTest, NumbersNeedDigits) {
    // Before a '.', after it, and after an exponent (RFC 8259, section 6).
    for (const char* text : {"-", "0E", "1e", "1e+", "1.", "-.5"}) {
        EXPECT_TRUE(refused(text)) << text;
    }

    EXPECT_TRUE(Json::parse("0e0").is_real());
    EXPECT_TRUE(Json::parse("-1.5E+3").is_real());
}

TEST(JsonTest, RealsReadBackAsTheSameReal) {
    constexpr double whole = 2.0;
    constexpr double precise = 0.1234567;
    constexpr double large = 1e20;

    EXPECT_EQ(Json(whole).dump(), "2.0");

    // Read back as a real, not an integer, and as the same real.
    for (const double value : {whole, precise, large, -whole}) {
        EXPECT_EQ(Json::parse(Json(value).dump()), Json(value)) << value;
    }
}

TEST(JsonTest, UnicodeEscapes) {
    EXPECT_EQ(Json::parse(R"("\u0041")").string(), "A");
    EXPECT_EQ(Json::parse(R"("\u00e9")").string(), "\xc3\xa9");
    EXPECT_EQ(Json::parse(R"("\u20AC")").string(), "\xe2\x82\xac");
    // A character beyond U+FFFF, as a UTF-16 surrogate pair.
    EXPECT_EQ(Json::parse(R"("\ud83d\ude00")").string(), "\xf0\x9f\x98\x80");
}

TEST(JsonTest, UnicodeEscapesRefused) {
    // Half a pair, too few hex digits, or one that is not hex.
    for (const char* text :
         {R"("\ud83d")", R"("\ude00")", R"("\ud83dx")", R"("\u12")", R"("\u12G4")"}) {
        EXPECT_TRUE(refused(text)) << text;
    }
}

TEST(JsonTest, ControlCharactersEscaped) {
    // A control character is written as an escape, and read back as itself.
    const Json control(std::string("\x01"));
    EXPECT_EQ(control.dump(), R"("\u0001")");
    EXPECT_EQ(Json::parse(control.dump()), control);
}

TEST(JsonTest, EscapesWritten) {
    const Json text(std::string("\"\\\b\f\n\r\t/"));

    EXPECT_EQ(text.dump(), R"("\"\\\b\f\n\r\t/")");
}

// An escape JSON does not name is read as the character escaped.
TEST(JsonTest, EscapesRead) {
    EXPECT_EQ(Json::parse(R"("\"\\\/\b\f\n\r\t")").string(), "\"\\/\b\f\n\r\t");
    EXPECT_EQ(Json::parse(R"("\x")").string(), "x");
}

TEST(JsonTest, MalformedTextRefused) {
    for (const char* text :
         {
             "",
             R"("open)",
             R"("open\)",
             "1 2",
             "@",
             "nul",
             "tru",
             "[1 2]",
             "[1,",
             "{1:2}",
             R"({"a" 1})",
             R"({"a":1 "b":2})",
             R"("\ud83d\u0041")",
         }) {
        EXPECT_TRUE(refused(text)) << text;
    }
}

TEST(JsonTest, EmptyContainers) {
    const auto object = Json::parse("{}");
    const auto array = Json::parse("[ ]");

    EXPECT_TRUE(object.is_object());
    EXPECT_TRUE(object.empty());
    EXPECT_EQ(object.dump(), "{}");
    EXPECT_EQ(object.dump(2), "{}");
    EXPECT_TRUE(array.is_array());
    EXPECT_EQ(array.dump(), "[]");
}

// An integer reads as a real too, but no other kind of value does.
TEST(JsonTest, NumbersAsReals) {
    constexpr auto number = 7;

    EXPECT_DOUBLE_EQ(Json(number).real(), 7.0);
    EXPECT_THROW((void)Json("seven").real(), std::domain_error);
    EXPECT_THROW((void)Json("seven").integer(), std::domain_error);
}

TEST(JsonTest, WrongKindOfValueRefused) {
    constexpr auto number = 7;
    Json value(number);
    const Json& const_value = value;

    EXPECT_THROW((void)value.array(), std::domain_error);
    EXPECT_THROW((void)value.object(), std::domain_error);
    EXPECT_THROW((void)const_value.array(), std::domain_error);
    EXPECT_THROW((void)const_value.object(), std::domain_error);
    EXPECT_THROW((void)value.size(), std::domain_error);
    EXPECT_THROW((void)value.empty(), std::domain_error);
    EXPECT_THROW((void)const_value.at(0), std::domain_error);
    EXPECT_THROW((void)value.at(0), std::domain_error);
    EXPECT_FALSE(value.has("key"));
}

// Indexing past the end of an array, or into null, grows it with nulls.
TEST(JsonTest, IndexingGrowsAnArray) {
    constexpr std::size_t third = 2;
    constexpr auto number = 7;
    Json value;

    value[third] = number;

    ASSERT_TRUE(value.is_array());
    EXPECT_EQ(value.size(), 3U);
    EXPECT_TRUE(value[0].is_null());
    EXPECT_EQ(value[third].integer(), number);
}

TEST(JsonTest, NonFiniteRealsNotWritten) {
    const Json infinite(std::numeric_limits<double>::infinity());
    const Json not_a_number(std::numeric_limits<double>::quiet_NaN());

    EXPECT_THROW((void)infinite.dump(), std::domain_error);
    EXPECT_THROW((void)not_a_number.dump(), std::domain_error);
}

// No arguments make null, and arguments that are not all [key, value] pairs
// make an array: a pair whose key is not a string, a list of one, or a value
// that is not a list.
TEST(JsonTest, InitializerListsThatMakeNoObject) {
    const Json none(std::initializer_list<Json>{});
    const Json keys_not_strings = {{1, 2}, {3, 4}};
    const Json too_short = {{"a", 1}, {"b"}};
    const Json not_a_list = {{"a", 1}, 1};

    EXPECT_TRUE(none.is_null());
    EXPECT_TRUE(keys_not_strings.is_array());
    EXPECT_TRUE(too_short.is_array());
    EXPECT_TRUE(not_a_list.is_array());
}

TEST(JsonTest, EmptyArray) {
    EXPECT_TRUE(Json::parse("[]").empty());
    EXPECT_FALSE(Json::parse("[1]").empty());
    EXPECT_FALSE(Json::parse(R"({"a":1})").empty());
}

TEST(JsonTest, AtFindsMembers) {
    Json value = {{"a", 1}, {"b", {2, 3}}};
    const Json& const_value = value;

    EXPECT_EQ(value.at("a").integer(), 1);
    EXPECT_EQ(const_value.at("a").integer(), 1);
    EXPECT_EQ(const_value.at("b").at(1).integer(), 3);
    EXPECT_THROW((void)value.at("c"), std::out_of_range);
    EXPECT_THROW((void)const_value.at("c"), std::out_of_range);
    EXPECT_THROW((void)value.at("b").at("a"), std::domain_error);
    EXPECT_THROW((void)const_value.at("b").at("a"), std::domain_error);
}

TEST(JsonTest, PrettyPrinted) {
    const Json value = {{"a", {1, 2}}, {"b", {{"c", true}}}, {"d", Json::Array()}};

    EXPECT_EQ(value.dump(2), "{\n"
                             "  \"a\": [\n"
                             "    1,\n"
                             "    2\n"
                             "  ],\n"
                             "  \"b\": {\n"
                             "    \"c\": true\n"
                             "  },\n"
                             "  \"d\": []\n"
                             "}");
    EXPECT_EQ(value.dump(), R"({"a":[1,2],"b":{"c":true},"d":[]})");
    EXPECT_EQ(Json::parse(value.dump(2)), value);
}

TEST(JsonTest, EveryKindOfWhitespaceSkipped) {
    EXPECT_EQ(Json::parse("\t[\r\n 1 ,\t2\r]\n"), Json({1, 2}));
}

TEST(JsonTest, ExponentsWithSigns) {
    EXPECT_DOUBLE_EQ(Json::parse("1e+2").real(), 100.0);
    EXPECT_DOUBLE_EQ(Json::parse("1E-2").real(), 0.01);
    EXPECT_TRUE(refused("1e+"));
    EXPECT_TRUE(refused("1e999"));
}

TEST(JsonTest, CodePointsPastTheSurrogates) {
    EXPECT_EQ(Json::parse(R"("\ue000")").string(), "\xee\x80\x80");
    EXPECT_EQ(Json::parse(R"("\uffff")").string(), "\xef\xbf\xbf");
}

TEST(JsonTest, HalfASurrogatePairFollowedByAnotherEscape) {
    // Another escape than \u, or one past the low surrogates.
    EXPECT_TRUE(refused(R"("\ud83d\n")"));
    EXPECT_TRUE(refused(R"("\ud83d\ue000")"));
}
