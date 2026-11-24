#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <cstddef>
#include <iterator>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "db/Database.hpp"
#include "stats/ContentUses.hpp"
#include "storage/Identifier.hpp"

using db::Database;
using db::SqliteError;
using stats::ContentUses;
using storage::Identifier;
using namespace std::chrono_literals;

namespace {

using Clock = ContentUses::Clock;
using Uses = ContentUses::Uses;

// SQLite keeps a database of this name in memory, for as long as it is
// open.
constexpr std::string_view in_memory = ":memory:";

// When the tests' clock starts, in whole seconds, as the table keeps times
// to the millisecond.
constexpr auto start = Clock::time_point(1'700'000'000s);

// A time between two milliseconds, which the table keeps as the first.
constexpr auto between_milliseconds = 1500us;

// Sizes of content as stored.
constexpr std::size_t small = 5;
constexpr std::size_t large = 1'048'576;

// The identifier of content whose hash is digit, over and over.
Identifier content(char digit) {
    return {"sha256", std::string(Identifier::longest_hash_digits(), digit)};
}

// Whether ContentUses refuses now as invalid.
bool refused(Database& database, const ContentUses::Now& now) {
    try {
        (void)ContentUses(database, now);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

// The uses, in a database in memory, with a clock that stands still until a
// test moves it on.
class ContentUsesTest : public testing::Test {
protected:
    ContentUsesTest();

    void advance(Clock::duration duration) noexcept;

    [[nodiscard]] Database& database() noexcept;
    [[nodiscard]] ContentUses& content_uses() noexcept;
    // What is known of the content of digit, which must be something.
    [[nodiscard]] Uses uses_of(char digit) const;
    // The identifier and size of each object held, sorted.
    [[nodiscard]] std::vector<std::string> held() const;

private:
    Database m_database;
    Clock::time_point m_now = start;
    ContentUses m_content_uses;
};

ContentUsesTest::ContentUsesTest()
    : m_database(in_memory, ContentUses::schema)
    , m_content_uses(m_database, [this] { return m_now; }) {}

void ContentUsesTest::advance(Clock::duration duration) noexcept {
    m_now += duration;
}

Database& ContentUsesTest::database() noexcept {
    return m_database;
}

ContentUses& ContentUsesTest::content_uses() noexcept {
    return m_content_uses;
}

Uses ContentUsesTest::uses_of(char digit) const {
    auto found = m_content_uses.uses(content(digit));

    if (!found) {
        throw std::logic_error(std::string("Nothing is known of the content of ") + digit);
    }

    return *found;
}

std::vector<std::string> ContentUsesTest::held() const {
    const auto objects = m_content_uses.held();
    std::vector<std::string> held;

    std::transform(objects.begin(), objects.end(), std::back_inserter(held),
                   [](const ContentUses::Held& object) {
        return object.identifier.to_string() + " " + std::to_string(object.uses.size.value_or(0));
    });
    std::sort(held.begin(), held.end());
    return held;
}

} // namespace

TEST_F(ContentUsesTest, NothingIsKnownOfContentNeitherStoredNorAskedFor) {
    EXPECT_FALSE(content_uses().uses(content('a')).has_value());
}

TEST_F(ContentUsesTest, ContentStoredIsNotYetAskedFor) {
    content_uses().record_stored(content('a'), small);

    const auto uses = uses_of('a');

    EXPECT_EQ(uses.size, small);
    EXPECT_EQ(uses.last_stored, start);
    EXPECT_FALSE(uses.last_requested.has_value());
    EXPECT_EQ(uses.requests, 0);
}

TEST_F(ContentUsesTest, ContentAskedForIsNotStored) {
    content_uses().record_requested(content('a'));

    const auto uses = uses_of('a');

    EXPECT_FALSE(uses.size.has_value());
    EXPECT_FALSE(uses.last_stored.has_value());
    EXPECT_EQ(uses.last_requested, start);
    EXPECT_EQ(uses.requests, 1);
}

TEST_F(ContentUsesTest, EachRequestIsCountedAndTheLastIsKept) {
    content_uses().record_stored(content('a'), small);
    advance(1s);
    content_uses().record_requested(content('a'));
    advance(1s);
    content_uses().record_requested(content('a'));

    const auto uses = uses_of('a');

    EXPECT_EQ(uses.size, small);
    EXPECT_EQ(uses.last_stored, start);
    EXPECT_EQ(uses.last_requested, start + 2s);
    EXPECT_EQ(uses.requests, 2);
}

TEST_F(ContentUsesTest, ContentStoredOnceAskedForKeepsItsRequests) {
    content_uses().record_requested(content('a'));
    advance(1s);
    content_uses().record_stored(content('a'), small);

    const auto uses = uses_of('a');

    EXPECT_EQ(uses.size, small);
    EXPECT_EQ(uses.last_stored, start + 1s);
    EXPECT_EQ(uses.last_requested, start);
    EXPECT_EQ(uses.requests, 1);
}

TEST_F(ContentUsesTest, ContentStoredAgainTakesItsNewSizeAndTime) {
    content_uses().record_stored(content('a'), small);
    content_uses().record_requested(content('a'));
    advance(1s);
    content_uses().record_stored(content('a'), large);

    const auto uses = uses_of('a');

    EXPECT_EQ(uses.size, large);
    EXPECT_EQ(uses.last_stored, start + 1s);
    EXPECT_EQ(uses.requests, 1);
}

TEST_F(ContentUsesTest, ContentDeletedIsNoLongerHeldButIsRemembered) {
    content_uses().record_stored(content('a'), small);
    content_uses().record_requested(content('a'));
    advance(1s);
    content_uses().record_deleted(content('a'));

    const auto uses = uses_of('a');

    EXPECT_FALSE(uses.size.has_value());
    EXPECT_EQ(uses.last_stored, start);
    EXPECT_EQ(uses.last_requested, start);
    EXPECT_EQ(uses.requests, 1);
    EXPECT_TRUE(held().empty());
}

TEST_F(ContentUsesTest, ContentStoredAgainOnceDeletedIsHeldAgain) {
    content_uses().record_stored(content('a'), small);
    content_uses().record_deleted(content('a'));
    content_uses().record_stored(content('a'), large);

    EXPECT_EQ(held(), std::vector<std::string>{content('a').to_string() + " 1048576"});
}

TEST_F(ContentUsesTest, OnlyWhatIsHeldIsListedAsHeld) {
    content_uses().record_stored(content('a'), small);
    content_uses().record_stored(content('b'), large);
    content_uses().record_requested(content('c'));
    content_uses().record_stored(content('d'), small);
    content_uses().record_deleted(content('d'));
    content_uses().record_deleted(content('e'));

    EXPECT_EQ(held(), (std::vector<std::string>{content('a').to_string() + " 5",
                                                content('b').to_string() + " 1048576"}));
    EXPECT_FALSE(content_uses().uses(content('e')).has_value());
}

TEST_F(ContentUsesTest, WhatIsHeldIsListedWithWhatIsKnownOfIt) {
    content_uses().record_stored(content('a'), small);
    advance(1s);
    content_uses().record_requested(content('a'));

    const auto held = content_uses().held();

    ASSERT_EQ(held.size(), 1U);
    EXPECT_EQ(held.at(0).identifier.to_string(), content('a').to_string());
    EXPECT_EQ(held.at(0).uses.size, small);
    EXPECT_EQ(held.at(0).uses.last_stored, start);
    EXPECT_EQ(held.at(0).uses.last_requested, start + 1s);
    EXPECT_EQ(held.at(0).uses.requests, 1);
}

TEST_F(ContentUsesTest, EachObjectIsKeptApart) {
    content_uses().record_stored(content('a'), small);
    content_uses().record_requested(content('b'));

    EXPECT_EQ(uses_of('a').requests, 0);
    EXPECT_FALSE(uses_of('b').size.has_value());
    EXPECT_FALSE(content_uses().uses(content('c')).has_value());
}

TEST_F(ContentUsesTest, TimesAreKeptToTheMillisecond) {
    advance(between_milliseconds);
    content_uses().record_requested(content('a'));

    EXPECT_EQ(uses_of('a').last_requested, start + 1ms);
}

// An identifier in upper case is the same content (HttpApi §5.4).
TEST_F(ContentUsesTest, AnIdentifierInUpperCaseIsTheSameContent) {
    const Identifier upper("sha256", std::string(Identifier::longest_hash_digits(), 'A'));

    content_uses().record_requested(upper);
    content_uses().record_requested(content('a'));

    EXPECT_EQ(uses_of('a').requests, 2);
}

TEST_F(ContentUsesTest, AClockIsNeeded) {
    EXPECT_TRUE(refused(database(), ContentUses::Now()));
    EXPECT_FALSE(refused(database(), &Clock::now));
}

TEST_F(ContentUsesTest, TheTableIsNeeded) {
    Database other(in_memory, "");
    ContentUses elsewhere(other);

    EXPECT_THROW(elsewhere.record_stored(content('a'), small), SqliteError);
    EXPECT_THROW(elsewhere.record_requested(content('a')), SqliteError);
    EXPECT_THROW((void)elsewhere.uses(content('a')), SqliteError);
    EXPECT_THROW(elsewhere.record_deleted(content('a')), SqliteError);
    EXPECT_THROW((void)elsewhere.held(), SqliteError);
}
