#include <gtest/gtest.h>
#include <sstream>
#include <string>
#include <vector>
#include <unordered_map>
#include <stdexcept>
#include "Json.hpp"

using namespace types;

TEST(JsonTest, ConstructorsAndTypes) {
    // Default constructor (null state)
    Json null_val;
    EXPECT_TRUE(null_val.is_null());
    EXPECT_FALSE(null_val.is_boolean());
    EXPECT_FALSE(null_val.is_number());
    EXPECT_FALSE(null_val.is_string());
    EXPECT_FALSE(null_val.is_array());
    EXPECT_FALSE(null_val.is_object());

    // Explicit nullptr constructor
    Json explicit_null(nullptr);
    EXPECT_TRUE(explicit_null.is_null());

    // Boolean constructors
    Json b_true(true);
    Json b_false(false);
    EXPECT_TRUE(b_true.is_boolean());
    EXPECT_TRUE(b_true.boolean());
    EXPECT_FALSE(b_false.boolean());

    // Numeric constructors (integers and floating point)
    Json int_val(42);
    Json int64_val(int64_t{-9223372036854775807LL});
    Json double_val(3.14159);

    EXPECT_TRUE(int_val.is_number());
    EXPECT_TRUE(int64_val.is_number());
    EXPECT_TRUE(double_val.is_number());
    EXPECT_EQ(int_val.integer(), 42);
    EXPECT_DOUBLE_EQ(double_val.real(), 3.14159);

    // String constructors (const char* and std::string)
    Json str_val("Hello, World!");
    std::string text = "C++17 JSON";
    Json str_obj_val(text);

    EXPECT_TRUE(str_val.is_string());
    EXPECT_TRUE(str_obj_val.is_string());
    EXPECT_EQ(str_val.string(), "Hello, World!");
    EXPECT_EQ(str_obj_val.string(), "C++17 JSON");
}

TEST(JsonTest, InitializerLists) {
    Json arr_val = {1, "two", 3.0, true, nullptr};
    EXPECT_TRUE(arr_val.is_array());
    EXPECT_EQ(arr_val.size(), 5U);
    EXPECT_EQ(arr_val[0].integer(), 1);
    EXPECT_EQ(arr_val[1].string(), "two");
    EXPECT_DOUBLE_EQ(arr_val[2].real(), 3.0);
    EXPECT_TRUE(arr_val[3].boolean());
    EXPECT_TRUE(arr_val[4].is_null());

    Json obj_val = {
        {"name", "Alice"},
        {"age", 30},
        {"is_admin", false},
        {"scores", {95, 88, 100}}};

    EXPECT_TRUE(obj_val.is_object());
    EXPECT_EQ(obj_val.size(), 4U);
    EXPECT_EQ(obj_val["name"].string(), "Alice");
    EXPECT_EQ(obj_val["age"].integer(), 30);
    EXPECT_FALSE(obj_val["is_admin"].boolean());
    EXPECT_TRUE(obj_val["scores"].is_array());
    EXPECT_EQ(obj_val["scores"].size(), 3U);
}

TEST(JsonTest, SubscriptAndAccess) {
    // Object access and implicit object creation
    Json doc;
    doc["user"] = "Bob";
    doc["active"] = true;
    doc["details"] = {{"role", "developer"}, {"level", 5}};

    EXPECT_TRUE(doc.is_object());
    EXPECT_EQ(doc["user"].string(), "Bob");
    EXPECT_EQ(doc["details"]["role"].string(), "developer");
    EXPECT_EQ(doc["details"]["level"].integer(), 5);

    // Array indexing
    Json arr = Json::Array({10, 20, 30});
    EXPECT_TRUE(arr.is_array());
    EXPECT_EQ(arr[0].integer(), 10);
    EXPECT_EQ(arr[1].integer(), 20);
    EXPECT_EQ(arr[2].integer(), 30);

    // Mutating elements via indexing
    arr[1] = 25;
    EXPECT_EQ(arr[1].integer(), 25);

    doc["user"] = "Charlie";
    EXPECT_EQ(doc["user"].string(), "Charlie");
}

TEST(JsonTest, ExceptionsAndTypeSafety) {
    Json num(100);

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
    EXPECT_THROW((void)arr.at(10), std::out_of_range);
}

TEST(JsonTest, SerializationAndStreams) {
    Json user = {
        {"id", 1001},
        {"active", true},
        {"tags", {"cpp", "json"}}};

    // Serialized output test
    std::string json_str = user.dump();
    EXPECT_FALSE(json_str.empty());

    // Stream operator test
    std::ostringstream oss;
    oss << user;
    EXPECT_EQ(oss.str(), json_str);
}

TEST(JsonTest, NestedComplexStructures) {
    Json root = {
        {"company", "TechCorp"},
        {"employees", {{{"id", 1}, {"name", "Alice"}, {"skills", {"C++", "Python"}}}, {{"id", 2}, {"name", "Bob"}, {"skills", {"Rust", "Go"}}}}}};

    EXPECT_TRUE(root["employees"].is_array());
    EXPECT_EQ(root["employees"].size(), 2U);
    EXPECT_EQ(root["employees"][0]["name"].string(), "Alice");
    EXPECT_EQ(root["employees"][1]["skills"][0].string(), "Rust");
}
