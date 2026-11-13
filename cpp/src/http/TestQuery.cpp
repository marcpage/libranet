#include <iostream>
#include <cassert>
#include <sstream>
#include <string>

#include "Query.hpp"

using namespace http;

void test_constructors_and_basic_accessors() {
    std::cout << "-> Testing constructors, setters, and basic accessors... " << std::flush;

    // Default constructor (empty state)
    Query query;
    assert(query.items().empty());
    assert(query.to_string().empty());
    assert(!query.get_first("foo").has_value());

    // Add and retrieve elements
    query.add("foo", "bar");
    assert(!query.items().empty());
    assert(query.items().size() == 1);

    auto val = query.get_first("foo");
    assert(val.has_value());
    assert(val.value() == "bar");

    // Removal and clearing
    query.remove("foo");
    assert(query.items().empty());

    query.add("a", "1");
    query.add("b", "2");
    assert(query.items().size() == 2);
    query.clear();
    assert(query.items().empty());

    std::cout << "PASSED\n";
}

void test_duplicate_keys_and_multivalue_getters() {
    std::cout << "-> Testing duplicate keys and multi-value query getters... " << std::flush;

    Query query = Query::parse("tag=cpp&tag=programming&tag=web&name=alice");

    // Test get_all for duplicate keys
    auto tags = query.get_all("tag");
    assert(tags.size() == 3);
    assert(tags[0] == "cpp");
    assert(tags[1] == "programming");
    assert(tags[2] == "web");

    // Test get_first returns the first occurrence
    auto first_tag = query.get_first("tag");
    assert(first_tag.has_value());
    assert(first_tag.value() == "cpp");

    // Test non-existent key retrieval
    assert(query.get_all("missing").empty());
    assert(!query.get_first("missing").has_value());

    std::cout << "PASSED\n";
}

void test_mutations_and_exclusive_set() {
    std::cout << "-> Testing mutations, additions, and exclusive set operations... " << std::flush;

    Query query;

    // Add duplicate keys
    query.add("item", "apple");
    query.add("item", "banana");
    assert(query.get_all("item").size() == 2);

    // Set item (replaces all occurrences of the key with a single value)
    query.set("item", "orange");
    assert(query.get_all("item").size() == 1);
    assert(query.get_first("item") == "orange");

    // Set a non-existent item acts like an add
    query.set("price", "99");
    assert(query.get_first("price") == "99");

    // Remove all instances of a key
    query.add("item", "grape");
    assert(query.get_all("item").size() == 2);
    query.remove("item");
    assert(query.get_all("item").empty());

    std::cout << "PASSED\n";
}

void test_stream_operators_and_parsing() {
    std::cout << "-> Testing stream I/O and query parsing utilities... " << std::flush;

    // Test static parsing with and without leading '?'
    Query q1 = Query::parse("?foo=bar&baz=123");
    assert(q1.get_first("foo") == "bar");
    assert(q1.get_first("baz") == "123");

    Query q2 = Query::parse("key=value_only");
    assert(q2.get_first("key") == "value_only");

    // Test serialization output format (to_string)
    std::string serialized = q1.to_string();
    assert(serialized == "foo=bar&baz=123");

    // Test ostream operator<<
    std::ostringstream oss;
    oss << q1;
    assert(oss.str() == "foo=bar&baz=123");

    // Test istream operator>>
    std::istringstream iss("alpha=one&beta=two");
    Query parsed_stream_query;
    iss >> parsed_stream_query;

    assert(!iss.fail());
    assert(parsed_stream_query.get_first("alpha") == "one");
    assert(parsed_stream_query.get_first("beta") == "two");

    std::cout << "PASSED\n";
}

int main() {
    std::cout << "     Running Query Test Suite       \n";

    test_constructors_and_basic_accessors();
    test_duplicate_keys_and_multivalue_getters();
    test_mutations_and_exclusive_set();
    test_stream_operators_and_parsing();

    std::cout << "   All Tests Passed Successfully!   \n";

    return 0;
}
