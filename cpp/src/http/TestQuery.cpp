#include <gtest/gtest.h>
#include <sstream>
#include <string>

#include "Query.hpp"

using namespace http;

TEST(QueryTest, ConstructorsAndBasicAccessors) {
    // Default constructor (empty state)
    Query query;
    EXPECT_TRUE(query.items().empty());
    EXPECT_TRUE(query.to_string().empty());
    EXPECT_FALSE(query.get_first("foo").has_value());

    // Add and retrieve elements
    query.add("foo", "bar");
    EXPECT_FALSE(query.items().empty());
    EXPECT_EQ(query.items().size(), 1U);

    auto val = query.get_first("foo");
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(val.value(), "bar");

    // Removal and clearing
    query.remove("foo");
    EXPECT_TRUE(query.items().empty());

    query.add("a", "1");
    query.add("b", "2");
    EXPECT_EQ(query.items().size(), 2U);
    query.clear();
    EXPECT_TRUE(query.items().empty());
}

TEST(QueryTest, DuplicateKeysAndMultivalueGetters) {
    Query query = Query::parse("tag=cpp&tag=programming&tag=web&name=alice");

    // Test get_all for duplicate keys
    auto tags = query.get_all("tag");
    ASSERT_EQ(tags.size(), 3U);
    EXPECT_EQ(tags[0], "cpp");
    EXPECT_EQ(tags[1], "programming");
    EXPECT_EQ(tags[2], "web");

    // Test get_first returns the first occurrence
    auto first_tag = query.get_first("tag");
    ASSERT_TRUE(first_tag.has_value());
    EXPECT_EQ(first_tag.value(), "cpp");

    // Test non-existent key retrieval
    EXPECT_TRUE(query.get_all("missing").empty());
    EXPECT_FALSE(query.get_first("missing").has_value());
}

TEST(QueryTest, MutationsAndExclusiveSet) {
    Query query;

    // Add duplicate keys
    query.add("item", "apple");
    query.add("item", "banana");
    EXPECT_EQ(query.get_all("item").size(), 2U);

    // Set item (replaces all occurrences of the key with a single value)
    query.set("item", "orange");
    EXPECT_EQ(query.get_all("item").size(), 1U);
    EXPECT_EQ(query.get_first("item"), "orange");

    // Set a non-existent item acts like an add
    query.set("price", "99");
    EXPECT_EQ(query.get_first("price"), "99");

    // Remove all instances of a key
    query.add("item", "grape");
    EXPECT_EQ(query.get_all("item").size(), 2U);
    query.remove("item");
    EXPECT_TRUE(query.get_all("item").empty());
}

TEST(QueryTest, StreamOperatorsAndParsing) {
    // Test static parsing with and without leading '?'
    Query q1 = Query::parse("?foo=bar&baz=123");
    EXPECT_EQ(q1.get_first("foo"), "bar");
    EXPECT_EQ(q1.get_first("baz"), "123");

    Query q2 = Query::parse("key=value_only");
    EXPECT_EQ(q2.get_first("key"), "value_only");

    // Test serialization output format (to_string)
    std::string serialized = q1.to_string();
    EXPECT_EQ(serialized, "foo=bar&baz=123");

    // Test ostream operator<<
    std::ostringstream oss;
    oss << q1;
    EXPECT_EQ(oss.str(), "foo=bar&baz=123");

    // Test istream operator>>
    std::istringstream iss("alpha=one&beta=two");
    Query parsed_stream_query;
    iss >> parsed_stream_query;

    EXPECT_FALSE(iss.fail());
    EXPECT_EQ(parsed_stream_query.get_first("alpha"), "one");
    EXPECT_EQ(parsed_stream_query.get_first("beta"), "two");
}
