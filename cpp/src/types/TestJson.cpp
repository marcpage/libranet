#include <iostream>
#include <cassert>
#include <sstream>
#include <string>
#include <vector>
#include <unordered_map>
#include <stdexcept>
#include "Json.hpp"

using namespace types;

void test_constructors_and_types() {
    std::cout << "-> Testing primitive constructors and type queries... " << std::flush;

    // Default constructor (null state)
    Json null_val;
    assert(null_val.is_null());
    assert(!null_val.is_boolean());
    assert(!null_val.is_number());
    assert(!null_val.is_string());
    assert(!null_val.is_array());
    assert(!null_val.is_object());

    // Explicit nullptr constructor
    Json explicit_null(nullptr);
    assert(explicit_null.is_null());

    // Boolean constructors
    Json b_true(true);
    Json b_false(false);
    assert(b_true.is_boolean());
    assert(b_true.boolean() == true);
    assert(b_false.boolean() == false);

    // Numeric constructors (integers and floating point)
    Json int_val(42);
    Json int64_val(int64_t{-9223372036854775807LL});
    Json double_val(3.14159);

    assert(int_val.is_number());
    assert(int64_val.is_number());
    assert(double_val.is_number());
    assert(int_val.integer() == 42);
    assert(double_val.real() == 3.14159);

    // String constructors (const char* and std::string)
    Json str_val("Hello, World!");
    std::string text = "C++17 JSON";
    Json str_obj_val(text);

    assert(str_val.is_string());
    assert(str_obj_val.is_string());
    assert(str_val.string() == "Hello, World!");
    assert(str_obj_val.string() == "C++17 JSON");

    std::cout << "PASSED\n";
}

void test_initializer_lists() {
    std::cout << "-> Testing initializer lists for arrays and objects... " << std::flush;

    Json arr_val = {1, "two", 3.0, true, nullptr};
    assert(arr_val.is_array());
    assert(arr_val.size() == 5);
    assert(arr_val[0].integer() == 1);
    assert(arr_val[1].string() == "two");
    assert(arr_val[2].real() == 3.0);
    assert(arr_val[3].boolean() == true);
    assert(arr_val[4].is_null());

    Json obj_val = {
        {"name", "Alice"},
        {"age", 30},
        {"is_admin", false},
        {"scores", {95, 88, 100}}
    };

    assert(obj_val.is_object());
    assert(obj_val.size() == 4);
    assert(obj_val["name"].string() == "Alice");
    assert(obj_val["age"].integer() == 30);
    assert(obj_val["is_admin"].boolean() == false);
    assert(obj_val["scores"].is_array());
    assert(obj_val["scores"].size() == 3);

    std::cout << "PASSED\n";
}

void test_subscript_and_access() {
    std::cout << "-> Testing subscript operations and element access... " << std::flush;

    // Object access and implicit object creation
    Json doc;
    doc["user"] = "Bob";
    doc["active"] = true;
    doc["details"] = {{"role", "developer"}, {"level", 5}};

    assert(doc.is_object());
    assert(doc["user"].string() == "Bob");
    assert(doc["details"]["role"].string() == "developer");
    assert(doc["details"]["level"].integer() == 5);

    // Array indexing
    Json arr = Json::Array({10, 20, 30});
    assert(arr.is_array());
    assert(arr[0].integer() == 10);
    assert(arr[1].integer() == 20);
    assert(arr[2].integer() == 30);

    // Mutating elements via indexing
    arr[1] = 25;
    assert(arr[1].integer() == 25);

    doc["user"] = "Charlie";
    assert(doc["user"].string() == "Charlie");

    std::cout << "PASSED\n";
}

void test_exceptions_and_type_safety() {
    std::cout << "-> Testing type safety and exception handling... " << std::flush;

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
    assert(caught_bad_string_cast);

    bool caught_bad_bool_cast = false;
    try {
        (void)num.boolean();
    } catch (const std::domain_error&) {
        caught_bad_bool_cast = true;
    } catch (const std::runtime_error&) {
        caught_bad_bool_cast = true;
    }
    assert(caught_bad_bool_cast);

    // Out-of-bounds array access check
    Json arr = {1, 2, 3};
    bool caught_out_of_bounds = false;
    try {
        (void)arr.at(10);
    } catch (const std::out_of_range&) {
        caught_out_of_bounds = true;
    }
    assert(caught_out_of_bounds);

    std::cout << "PASSED\n";
}

void test_serialization_and_streams() {
    std::cout << "-> Testing serialization and stream operators... " << std::flush;

    Json user = {
        {"id", 1001},
        {"active", true},
        {"tags", {"cpp", "json"}}
    };

    // Serialized output test
    std::string json_str = user.dump();
    assert(!json_str.empty());

    // Stream operator test
    std::ostringstream oss;
    oss << user;
    assert(oss.str() == json_str);

    std::cout << "PASSED\n";
}

void test_nested_complex_structures() {
    std::cout << "-> Testing deep nesting and complex structures... " << std::flush;

    Json root = {
        {"company", "TechCorp"},
        {"employees", {
            {
                {"id", 1},
                {"name", "Alice"},
                {"skills", {"C++", "Python"}}
            },
            {
                {"id", 2},
                {"name", "Bob"},
                {"skills", {"Rust", "Go"}}
            }
        }}
    };

    assert(root["employees"].is_array());
    assert(root["employees"].size() == 2);
    assert(root["employees"][0]["name"].string() == "Alice");
    assert(root["employees"][1]["skills"][0].string() == "Rust");

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "      Running JSON Test Suite       \n";

    test_constructors_and_types();
    test_initializer_lists();
    test_subscript_and_access();
    test_exceptions_and_type_safety();
    test_serialization_and_streams();
    test_nested_complex_structures();

    std::cout << "   All Tests Passed Successfully!   \n";

    return 0;
}
