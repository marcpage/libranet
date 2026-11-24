#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "db/Database.hpp"
#include "db/Statement.hpp"
#include "protocol/SeekList.hpp"
#include "stats/Sought.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"

using db::Database;
using db::SqliteError;
using db::Statement;
using stats::Sought;
using storage::HashPrefix;
using storage::Identifier;
using namespace std::chrono_literals;

namespace {

using Clock = Sought::Clock;
using Texts = std::vector<std::string>;

// SQLite keeps a database of this name in memory, for as long as it is
// open.
constexpr std::string_view in_memory = ":memory:";

// When the tests' clock starts, in whole seconds, as the table keeps times
// to the millisecond.
constexpr auto start = Clock::time_point(1'700'000'000s);

// How long the tests seek each entry for.
constexpr auto entry_ttl = 10s;

// The content whose hash is digit, over and over.
Identifier content(char digit) {
    return {"sha256", std::string(Identifier::longest_hash_digits(), digit)};
}

// The prefix of text.
HashPrefix prefix(std::string_view text) {
    return HashPrefix::parse(text);
}

// The text of each of entries, in order.
template<typename Entry>
Texts texts_of(const std::vector<Entry>& entries) {
    Texts texts;

    std::transform(entries.begin(), entries.end(), std::back_inserter(texts),
                   [](const Entry& entry) { return entry.to_string(); });
    return texts;
}

// Whether Sought refuses ttl or clock as invalid.
bool refused(Database& database, Sought::Milliseconds ttl,
             const Sought::ClockFunction& clock = &Clock::now) {
    try {
        (void)Sought(database, ttl, clock);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

// What is sought, in a database in memory, with a clock that stands still
// until a test moves it on.
class SoughtTest : public testing::Test {
protected:
    SoughtTest();

    void advance(Clock::duration duration) noexcept;

    [[nodiscard]] Database& database() noexcept;
    [[nodiscard]] Sought& sought() noexcept;
    // The text of each identifier and prefix the seek list names, in order.
    [[nodiscard]] Texts data() const;
    [[nodiscard]] Texts search() const;
    // The value of each row the table holds, whether listed or not, in
    // order.
    [[nodiscard]] Texts rows();

private:
    Database m_database;
    Clock::time_point m_now = start;
    Sought m_sought;
};

SoughtTest::SoughtTest()
    : m_database(in_memory, Sought::schema)
    , m_sought(m_database, entry_ttl, [this] { return m_now; }) {}

void SoughtTest::advance(Clock::duration duration) noexcept {
    m_now += duration;
}

Database& SoughtTest::database() noexcept {
    return m_database;
}

Sought& SoughtTest::sought() noexcept {
    return m_sought;
}

Texts SoughtTest::data() const {
    return texts_of(m_sought.seek_list().data());
}

Texts SoughtTest::search() const {
    return texts_of(m_sought.seek_list().search());
}

Texts SoughtTest::rows() {
    Statement statement(m_database, "SELECT value FROM sought ORDER BY value");
    Texts values;

    while (statement.step()) {
        values.push_back(statement.text(0));
    }

    return values;
}

} // namespace

TEST_F(SoughtTest, NothingIsSoughtAtFirst) {
    EXPECT_TRUE(data().empty());
    EXPECT_TRUE(search().empty());
}

TEST_F(SoughtTest, ContentSoughtIsListedAsData) {
    EXPECT_TRUE(sought().seek(content('a')));
    EXPECT_EQ(data(), Texts{content('a').to_string()});
    EXPECT_TRUE(search().empty());
}

TEST_F(SoughtTest, APrefixSoughtIsListedAsASearch) {
    EXPECT_TRUE(sought().seek(prefix("0123ABCD")));
    EXPECT_TRUE(data().empty());
    EXPECT_EQ(search(), Texts{"0123abcd"});
}

TEST_F(SoughtTest, WhatIsSoughtAgainIsNotNewAndListedOnce) {
    (void)sought().seek(content('a'));
    (void)sought().seek(prefix("0123"));
    advance(1s);

    EXPECT_FALSE(sought().seek(content('a')));
    EXPECT_FALSE(sought().seek(prefix("0123")));
    EXPECT_EQ(data(), Texts{content('a').to_string()});
    EXPECT_EQ(search(), Texts{"0123"});
}

TEST_F(SoughtTest, TheMostRecentlySoughtComeFirst) {
    (void)sought().seek(content('a'));
    (void)sought().seek(prefix("aa"));
    advance(1s);
    (void)sought().seek(content('c'));
    (void)sought().seek(content('b'));
    (void)sought().seek(prefix("cc"));
    (void)sought().seek(prefix("bb"));
    advance(1s);
    (void)sought().seek(content('d'));
    (void)sought().seek(prefix("dd"));
    advance(1s);
    (void)sought().seek(content('a'));

    // Those sought at the same time are in the order of their text.
    EXPECT_EQ(data(), (Texts{content('a').to_string(), content('d').to_string(),
                             content('b').to_string(), content('c').to_string()}));
    EXPECT_EQ(search(), (Texts{"dd", "bb", "cc", "aa"}));
}

TEST_F(SoughtTest, ContentFoundIsNoLongerSought) {
    (void)sought().seek(content('a'));
    (void)sought().seek(content('b'));

    EXPECT_TRUE(sought().found(content('a')));
    EXPECT_EQ(data(), Texts{content('b').to_string()});
    EXPECT_FALSE(sought().found(content('a')));
}

TEST_F(SoughtTest, ContentFoundThatWasNotSoughtChangesNothing) {
    (void)sought().seek(content('a'));

    EXPECT_FALSE(sought().found(content('b')));
    EXPECT_EQ(data(), Texts{content('a').to_string()});
}

TEST_F(SoughtTest, ContentFoundLeavesTheSearchesItMatchesSought) {
    (void)sought().seek(prefix("aaaa"));
    (void)sought().seek(content('a'));

    EXPECT_TRUE(sought().found(content('a')));
    EXPECT_TRUE(data().empty());
    EXPECT_EQ(search(), Texts{"aaaa"});
}

TEST_F(SoughtTest, AnEntryIsListedUntilItsTtlHasPassed) {
    (void)sought().seek(content('a'));
    (void)sought().seek(prefix("0123"));
    advance(entry_ttl - 1ms);

    EXPECT_EQ(data(), Texts{content('a').to_string()});
    EXPECT_EQ(search(), Texts{"0123"});

    advance(1ms);

    EXPECT_TRUE(data().empty());
    EXPECT_TRUE(search().empty());
}

TEST_F(SoughtTest, AnEntrySoughtAgainIsListedForItsTtlFromThen) {
    (void)sought().seek(content('a'));
    advance(entry_ttl - 1s);
    (void)sought().seek(content('a'));
    advance(entry_ttl - 1s);

    EXPECT_EQ(data(), Texts{content('a').to_string()});
}

TEST_F(SoughtTest, AnEntryWhoseTtlHasPassedIsNewWhenSoughtAgain) {
    (void)sought().seek(content('a'));
    (void)sought().seek(prefix("0123"));
    advance(entry_ttl);

    EXPECT_TRUE(sought().seek(content('a')));
    EXPECT_TRUE(sought().seek(prefix("0123")));
    EXPECT_EQ(data(), Texts{content('a').to_string()});
}

TEST_F(SoughtTest, ContentFoundOnceItsTtlHasPassedWasNotListed) {
    (void)sought().seek(content('a'));
    advance(entry_ttl);

    EXPECT_FALSE(sought().found(content('a')));
    EXPECT_TRUE(rows().empty());
}

TEST_F(SoughtTest, OnlyWhatHasExpiredIsForgotten) {
    (void)sought().seek(content('a'));
    (void)sought().seek(prefix("aa"));
    advance(1s);
    (void)sought().seek(content('b'));
    (void)sought().seek(prefix("bb"));
    advance(entry_ttl - 1s);

    EXPECT_EQ(rows().size(), 4U);

    sought().forget_expired();

    EXPECT_EQ(rows(), (Texts{"bb", content('b').to_string()}));
    EXPECT_EQ(data(), Texts{content('b').to_string()});
    EXPECT_EQ(search(), Texts{"bb"});
}

TEST_F(SoughtTest, ATtlNotMoreThanZeroIsRefused) {
    EXPECT_TRUE(refused(database(), 0ms));
    EXPECT_TRUE(refused(database(), -1ms));
    EXPECT_FALSE(refused(database(), 1ms));
}

TEST_F(SoughtTest, AClockIsNeeded) {
    EXPECT_TRUE(refused(database(), entry_ttl, Sought::ClockFunction()));
}

TEST_F(SoughtTest, TheTableIsNeeded) {
    Database other(in_memory, "");
    Sought elsewhere(other, entry_ttl);

    EXPECT_THROW((void)elsewhere.seek(content('a')), SqliteError);
    EXPECT_THROW((void)elsewhere.seek(prefix("0123")), SqliteError);
    EXPECT_THROW((void)elsewhere.found(content('a')), SqliteError);
    EXPECT_THROW(elsewhere.forget_expired(), SqliteError);
    EXPECT_THROW((void)elsewhere.seek_list(), SqliteError);
}

TEST_F(SoughtTest, ARowThatIsNoIdentifierIsRefused) {
    (void)sought().seek(content('a'));
    database().execute("UPDATE sought SET value = 'sha256/not-a-hash'");

    EXPECT_THROW((void)sought().seek_list(), std::invalid_argument);
}

TEST_F(SoughtTest, ARowThatIsNoPrefixIsRefused) {
    (void)sought().seek(prefix("0123"));
    database().execute("UPDATE sought SET value = 'not hex'");

    EXPECT_THROW((void)sought().seek_list(), std::invalid_argument);
}
