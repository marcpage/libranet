#include <gtest/gtest.h>
#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <sstream>
#include <stdexcept>
#include <string>

#include "types/Json.hpp"

using types::Json;

namespace {

// Whether Json::parse turns down text as invalid.
bool refused(const std::string& text) {
    try {
        static_cast<void>(Json::parse(text));
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

} // namespace

TEST(JsonTest, ConstructorsAndTypes) {
    // Default constructor (null state)
    const Json null_val;
    EXPECT_TRUE(null_val.is_null());
    EXPECT_FALSE(null_val.is_boolean());
    EXPECT_FALSE(null_val.is_number());
    EXPECT_FALSE(null_val.is_string());
    EXPECT_FALSE(null_val.is_array());
    EXPECT_FALSE(null_val.is_object());

    // Explicit nullptr constructor
    const Json explicit_null(nullptr);
    EXPECT_TRUE(explicit_null.is_null());

    // Boolean constructors
    const Json b_true(true);
    const Json b_false(false);
    EXPECT_TRUE(b_true.is_boolean());
    EXPECT_TRUE(b_true.boolean());
    EXPECT_FALSE(b_false.boolean());

    // Numeric constructors (integers and floating point)
    constexpr auto int_value = 42;
    constexpr int64_t int64_value = -9223372036854775807LL;
    constexpr auto double_value = 3.14159;
    const Json int_val(int_value);
    const Json int64_val(int64_value);
    const Json double_val(double_value);

    EXPECT_TRUE(int_val.is_number());
    EXPECT_TRUE(int64_val.is_number());
    EXPECT_TRUE(double_val.is_number());
    EXPECT_EQ(int_val.integer(), int_value);
    EXPECT_DOUBLE_EQ(double_val.real(), double_value);

    // String constructors (const char* and std::string)
    const Json str_val("Hello, World!");
    const std::string text = "C++17 JSON";
    const Json str_obj_val(text);

    EXPECT_TRUE(str_val.is_string());
    EXPECT_TRUE(str_obj_val.is_string());
    EXPECT_EQ(str_val.string(), "Hello, World!");
    EXPECT_EQ(str_obj_val.string(), "C++17 JSON");
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
    Json arr_val = {1, "two", real_value, true, nullptr};
    EXPECT_TRUE(arr_val.is_array());
    EXPECT_EQ(arr_val.size(), 5U);
    EXPECT_EQ(arr_val[0].integer(), 1);
    EXPECT_EQ(arr_val[1].string(), "two");
    EXPECT_DOUBLE_EQ(arr_val[2].real(), real_value);
    EXPECT_TRUE(arr_val[3].boolean());
    EXPECT_TRUE(arr_val[4].is_null());

    constexpr auto age = 30;
    constexpr auto first_score = 95;
    constexpr auto second_score = 88;
    constexpr auto third_score = 100;
    Json obj_val = {
        {"name", "Alice"},
        {"age", age},
        {"is_admin", false},
        {"scores", {first_score, second_score, third_score}},
    };

    EXPECT_TRUE(obj_val.is_object());
    EXPECT_EQ(obj_val.size(), 4U);
    EXPECT_EQ(obj_val["name"].string(), "Alice");
    EXPECT_EQ(obj_val["age"].integer(), age);
    EXPECT_FALSE(obj_val["is_admin"].boolean());
    EXPECT_TRUE(obj_val["scores"].is_array());
    EXPECT_EQ(obj_val["scores"].size(), 3U);
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
    Json arr = Json::Array({first_value, second_value, third_value});
    EXPECT_TRUE(arr.is_array());
    EXPECT_EQ(arr[0].integer(), first_value);
    EXPECT_EQ(arr[1].integer(), second_value);
    EXPECT_EQ(arr[2].integer(), third_value);

    // Mutating elements via indexing
    constexpr auto replacement_value = 25;
    arr[1] = replacement_value;
    EXPECT_EQ(arr[1].integer(), replacement_value);

    doc["user"] = "Charlie";
    EXPECT_EQ(doc["user"].string(), "Charlie");
}

// This test's try and catch blocks, and gtest's assertion macros, which count
// as branches, put it over this check's limit.
// NOLINTBEGIN(readability-function-cognitive-complexity)
TEST(JsonTest, ExceptionsAndTypeSafety) {
    constexpr auto number = 100;
    const Json num(number);

    // Expect std::domain_error or std::runtime_error when requesting bad conversions
    bool caught_bad_string_cast = false;
    try {
        (void)num.string();
    } catch (const std::domain_error&) {
        caught_bad_string_cast = true;
    } catch (const std::runtime_error&) {
        caught_bad_string_cast = true;
    }
    EXPECT_TRUE(caught_bad_string_cast);

    bool caught_bad_bool_cast = false;
    try {
        (void)num.boolean();
    } catch (const std::domain_error&) {
        caught_bad_bool_cast = true;
    } catch (const std::runtime_error&) {
        caught_bad_bool_cast = true;
    }
    EXPECT_TRUE(caught_bad_bool_cast);

    // Out-of-bounds array access check
    Json arr = {1, 2, 3};
    constexpr std::size_t out_of_range_index = 10;
    EXPECT_THROW((void)arr.at(out_of_range_index), std::out_of_range);
}
// NOLINTEND(readability-function-cognitive-complexity)

TEST(JsonTest, SerializationAndStreams) {
    constexpr auto user_id = 1001;
    const Json user = {
        {"id", user_id},
        {"active", true},
        {"tags", {"cpp", "json"}},
    };

    // Serialized output test
    const std::string json_str = user.dump();
    EXPECT_FALSE(json_str.empty());

    // Stream operator test
    std::ostringstream oss;
    oss << user;
    EXPECT_EQ(oss.str(), json_str);
}

TEST(JsonTest, NestedComplexStructures) {
    Json root = {
        {"company", "TechCorp"},
        {"employees", {{{"id", 1}, {"name", "Alice"}, {"skills", {"C++", "Python"}}}, {{"id", 2}, {"name", "Bob"}, {"skills", {"Rust", "Go"}}}}},
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
    for (const char* text : {R"("\ud83d")", R"("\ude00")", R"("\ud83dx")", R"("\u12")", R"("\u12G4")"}) {
        EXPECT_TRUE(refused(text)) << text;
    }
}

TEST(JsonTest, ControlCharactersEscaped) {
    // A control character is written as an escape, and read back as itself.
    const Json control(std::string("\x01"));
    EXPECT_EQ(control.dump(), R"("\u0001")");
    EXPECT_EQ(Json::parse(control.dump()), control);
}
