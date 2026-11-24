#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <cstddef>
#include <iomanip>
#include <ios>
#include <iterator>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"
#include "storage/SearchAnswers.hpp"

using namespace std::chrono_literals;
using storage::HashPrefix;
using storage::Identifier;
using storage::SearchAnswers;

namespace {

// storage.search_min_bits and storage.search_cache_ttl_seconds, as the
// Python node has them by default.
constexpr std::size_t min_bits = 24;
constexpr auto ttl = 5min;

// The SHA-256 hash that starts with digits, and is zeros after them.
std::string hash_of(const std::string& digits) {
    return digits + std::string(Identifier::longest_hash_digits() - digits.size(), '0');
}

// The identifier of the SHA-256 hash that starts with digits, and is zeros
// after them.
Identifier identifier_of(const std::string& digits) {
    return {"sha256", hash_of(digits)};
}

// value in hex, as many digits as width, with zeros before it.
std::string hex(std::size_t value, int width) {
    std::ostringstream text;

    text << std::hex << std::setw(width) << std::setfill('0') << value;
    return text.str();
}

// The hash of each of identifiers, in order.
std::vector<std::string> hashes(const std::vector<Identifier>& identifiers) {
    std::vector<std::string> found(identifiers.size());

    std::transform(identifiers.begin(), identifiers.end(), found.begin(),
                   [](const Identifier& identifier) { return identifier.hash(); });
    return found;
}

// What a node holds, listed by what each hash begins with, as a store lists
// it. It remembers which prefixes it was asked for.
class Store {
public:
    explicit Store(std::vector<Identifier> held = {});

    void put(Identifier identifier);
    [[nodiscard]] SearchAnswers::Held lister();
    [[nodiscard]] const std::vector<std::string>& asked() const noexcept;

private:
    std::vector<Identifier> m_held;
    std::vector<std::string> m_asked;

    [[nodiscard]] std::vector<Identifier> _begins_with(const HashPrefix& prefix);
};

Store::Store(std::vector<Identifier> held)
    : m_held(std::move(held)) {}

void Store::put(Identifier identifier) {
    m_held.push_back(std::move(identifier));
}

SearchAnswers::Held Store::lister() {
    return [this](const HashPrefix& prefix) { return _begins_with(prefix); };
}

const std::vector<std::string>& Store::asked() const noexcept {
    return m_asked;
}

std::vector<Identifier> Store::_begins_with(const HashPrefix& prefix) {
    std::vector<Identifier> found;

    m_asked.push_back(prefix.to_string());
    std::copy_if(m_held.begin(), m_held.end(), std::back_inserter(found),
                 [&prefix](const Identifier& identifier) {
        return 0 == identifier.hash().rfind(prefix.to_string(), 0);
    });
    return found;
}

// A clock that stands still until it is moved on.
class StoppedClock {
public:
    [[nodiscard]] SearchAnswers::ClockFunction function() const;
    void advance(SearchAnswers::Clock::duration duration);

private:
    SearchAnswers::Clock::time_point m_now;
};

SearchAnswers::ClockFunction StoppedClock::function() const {
    return [this] { return m_now; };
}

void StoppedClock::advance(SearchAnswers::Clock::duration duration) {
    m_now += duration;
}

// The answers to searches of store, at the time clock says.
SearchAnswers answers_of(Store& store, const StoppedClock& clock, std::size_t bits = min_bits) {
    return {store.lister(), bits, ttl, clock.function()};
}

// Whether SearchAnswers refuses held, ttl, and clock as invalid.
bool refused(const SearchAnswers::Held& held, SearchAnswers::Milliseconds keep_for,
             const SearchAnswers::ClockFunction& clock) {
    try {
        (void)SearchAnswers(held, min_bits, keep_for, clock);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

} // namespace

// What shares the fewest bits that match comes last, and what shares fewer
// is left out (HttpApi §6).
TEST(SearchAnswersTest, HeldMatchesBestFirst) {
    constexpr std::size_t twelve_bits = 12;
    Store store({
        identifier_of("abc1ff"),
        identifier_of("abd"),
        identifier_of("abc124"),
        identifier_of("000"),
        identifier_of("abc123"),
    });
    const StoppedClock clock;
    auto answers = answers_of(store, clock, twelve_bits);

    const auto found = answers.answer(HashPrefix::parse("abc123"));

    EXPECT_EQ(hashes(found),
              (std::vector<std::string>{hash_of("abc123"), hash_of("abc124"), hash_of("abc1ff")}));
}

// A match need not share a whole digit more: abc3 shares 14 bits with
// abc1, abc4 only 13.
TEST(SearchAnswersTest, MinBitsNeedNotFillADigit) {
    constexpr std::size_t fourteen_bits = 14;
    Store store({identifier_of("abc4"), identifier_of("abc3"), identifier_of("abc123")});
    const StoppedClock clock;
    auto answers = answers_of(store, clock, fourteen_bits);

    const auto found = answers.answer(HashPrefix::parse("abc123"));

    EXPECT_EQ(hashes(found), (std::vector<std::string>{hash_of("abc123"), hash_of("abc3")}));
    EXPECT_EQ(store.asked(), (std::vector<std::string>{"abc"}));
}

// A prefix of fewer bits than a match must share is matched by all of it,
// and what matches as well is in order of its hash.
TEST(SearchAnswersTest, ShorterPrefixMatchesAllOfIt) {
    Store store({identifier_of("ab12"), identifier_of("ac"), identifier_of("ab"), identifier_of("a")});
    const StoppedClock clock;
    auto answers = answers_of(store, clock);

    const auto found = answers.answer(HashPrefix::parse("ab"));

    EXPECT_EQ(hashes(found), (std::vector<std::string>{hash_of("ab"), hash_of("ab12")}));
    EXPECT_EQ(store.asked(), (std::vector<std::string>{"ab"}));
}

// abcdee shares 23 bits with abcdef, one too few.
TEST(SearchAnswersTest, NothingNearEnoughIsAnEmptyAnswer) {
    Store store({identifier_of("abcdee")});
    const StoppedClock clock;
    auto answers = answers_of(store, clock);

    const auto found = answers.answer(HashPrefix::parse(hash_of("abcdef")));

    EXPECT_TRUE(found.empty());
    EXPECT_EQ(store.asked(), (std::vector<std::string>{"abcdef"}));
}

// When a match must share less than a digit, the store is asked for each
// first digit that shares it: those of 8, 1000, that share two bits are
// 10xx.
TEST(SearchAnswersTest, LessThanADigitScansEachDigitThatMatches) {
    constexpr std::size_t two_bits = 2;
    Store none_store;
    Store two_store;
    const StoppedClock clock;
    auto none = answers_of(none_store, clock, 0);
    auto two = answers_of(two_store, clock, two_bits);

    (void)none.answer(HashPrefix::parse("8"));
    (void)two.answer(HashPrefix::parse("8"));

    EXPECT_EQ(none_store.asked(), (std::vector<std::string>{"0", "1", "2", "3", "4", "5", "6", "7",
                                                            "8", "9", "a", "b", "c", "d", "e",
                                                            "f"}));
    EXPECT_EQ(two_store.asked(), (std::vector<std::string>{"8", "9", "a", "b"}));
}

// Content stored after an answer was made is not listed until the answer
// has gone ttl unchanged (HttpApi §6).
TEST(SearchAnswersTest, KeptUntilUnchangedForTtl) {
    Store store({identifier_of("abcdef")});
    StoppedClock clock;
    auto answers = answers_of(store, clock);
    const auto prefix = HashPrefix::parse("abcdef");

    (void)answers.answer(prefix);
    store.put(identifier_of("abcdef1"));
    clock.advance(ttl - 1ms);
    const auto kept = answers.answer(prefix);
    clock.advance(1ms);
    const auto made_again = answers.answer(prefix);

    EXPECT_EQ(hashes(kept), (std::vector<std::string>{hash_of("abcdef")}));
    EXPECT_EQ(hashes(made_again),
              (std::vector<std::string>{hash_of("abcdef"), hash_of("abcdef1")}));
    EXPECT_EQ(store.asked().size(), 2U);
}

TEST(SearchAnswersTest, EachPrefixIsKeptForItsOwnTtl) {
    Store store({identifier_of("abcdef"), identifier_of("123456")});
    StoppedClock clock;
    auto answers = answers_of(store, clock);
    const auto first = HashPrefix::parse("abcdef");
    const auto second = HashPrefix::parse("123456");

    (void)answers.answer(first);
    clock.advance(ttl - 1ms);
    (void)answers.answer(second);
    clock.advance(1ms);
    (void)answers.answer(first);
    const auto kept = answers.answer(second);

    EXPECT_EQ(hashes(kept), (std::vector<std::string>{hash_of("123456")}));
    EXPECT_EQ(store.asked(), (std::vector<std::string>{"abcdef", "123456", "abcdef"}));
}

// What a peer names is ranked with what is held, and what does not match,
// abcde0, sharing 20 bits, is left out, as is what is named already.
TEST(SearchAnswersTest, WhatPeersNameIsAdded) {
    Store store({identifier_of("abcdef")});
    const StoppedClock clock;
    auto answers = answers_of(store, clock);
    const auto prefix = HashPrefix::parse("abcdef8");

    (void)answers.answer(prefix);
    answers.add(prefix, {identifier_of("abcde0"), identifier_of("abcdef8"), identifier_of("abcdef")});
    const auto found = answers.answer(prefix);

    EXPECT_EQ(hashes(found), (std::vector<std::string>{hash_of("abcdef8"), hash_of("abcdef")}));
    EXPECT_EQ(store.asked().size(), 1U);
}

TEST(SearchAnswersTest, AddingToNoAnswerMakesOne) {
    Store store({identifier_of("abcdef")});
    const StoppedClock clock;
    auto answers = answers_of(store, clock);
    const auto prefix = HashPrefix::parse("abcdef");

    answers.add(prefix, {identifier_of("abcdef8")});
    const auto found = answers.answer(prefix);

    EXPECT_EQ(hashes(found), (std::vector<std::string>{hash_of("abcdef"), hash_of("abcdef8")}));
    EXPECT_EQ(store.asked().size(), 1U);
}

// An answer is kept for ttl after it last changed, not after it was made.
TEST(SearchAnswersTest, AddingMoreKeepsItLonger) {
    Store store({identifier_of("abcdef")});
    StoppedClock clock;
    auto answers = answers_of(store, clock);
    const auto prefix = HashPrefix::parse("abcdef");

    (void)answers.answer(prefix);
    clock.advance(ttl - 1ms);
    answers.add(prefix, {identifier_of("abcdef8")});
    clock.advance(ttl - 1ms);
    const auto kept = answers.answer(prefix);
    clock.advance(1ms);
    const auto made_again = answers.answer(prefix);

    EXPECT_EQ(hashes(kept), (std::vector<std::string>{hash_of("abcdef"), hash_of("abcdef8")}));
    EXPECT_EQ(hashes(made_again), (std::vector<std::string>{hash_of("abcdef")}));
    EXPECT_EQ(store.asked().size(), 2U);
}

TEST(SearchAnswersTest, AddingNothingNewKeepsItNoLonger) {
    Store store({identifier_of("abcdef")});
    StoppedClock clock;
    auto answers = answers_of(store, clock);
    const auto prefix = HashPrefix::parse("abcdef");

    (void)answers.answer(prefix);
    clock.advance(ttl - 1ms);
    answers.add(prefix, {identifier_of("abcdef"), identifier_of("abcde0")});
    clock.advance(1ms);
    (void)answers.answer(prefix);

    EXPECT_EQ(store.asked().size(), 2U);
}

// What is named past the most an answer keeps is left out, the worst
// first: all share the prefix's four bits, so the last in order goes.
TEST(SearchAnswersTest, KeepsNoMoreThanItsMost) {
    constexpr int digits = 5;
    Store store;
    const StoppedClock clock;
    auto answers = answers_of(store, clock);
    const auto prefix = HashPrefix::parse("0");
    std::vector<Identifier> named;

    for (std::size_t index = 0; index <= SearchAnswers::max_identifiers; ++index) {
        named.push_back(identifier_of("0" + hex(index, digits)));
    }

    answers.add(prefix, named);
    const auto found = answers.answer(prefix);

    ASSERT_EQ(found.size(), SearchAnswers::max_identifiers);
    EXPECT_EQ(found.front().hash(), hash_of("000000"));
    EXPECT_EQ(found.back().hash(), hash_of("00ffff"));
}

// Several threads searching and adding at once, which ThreadSanitizer
// checks, lose nothing any of them added.
TEST(SearchAnswersTest, SearchesFromManyThreads) {
    constexpr std::size_t thread_count = 4;
    constexpr std::size_t adds_per_thread = 50;
    constexpr int digits = 4;
    SearchAnswers answers([](const HashPrefix&) { return std::vector<Identifier>(); }, min_bits,
                          ttl);
    const auto prefix = HashPrefix::parse("abcdef");
    std::vector<std::thread> threads;

    threads.reserve(thread_count);

    for (std::size_t thread_index = 0; thread_index < thread_count; ++thread_index) {
        threads.emplace_back([&, thread_index] {
            for (std::size_t index = 0; index < adds_per_thread; ++index) {
                const auto added = (thread_index * adds_per_thread) + index;

                answers.add(prefix, {identifier_of("abcdef" + hex(added, digits))});
                (void)answers.answer(prefix);
            }
        });
    }

    for (auto& thread : threads) {
        thread.join();
    }

    EXPECT_EQ(answers.answer(prefix).size(), thread_count * adds_per_thread);
}

TEST(SearchAnswersTest, MissingFunctionsOrNoTtlAreRefused) {
    const SearchAnswers::Held held = [](const HashPrefix&) { return std::vector<Identifier>(); };
    const SearchAnswers::ClockFunction clock = SearchAnswers::Clock::now;

    EXPECT_TRUE(refused(SearchAnswers::Held(), ttl, clock));
    EXPECT_TRUE(refused(held, ttl, SearchAnswers::ClockFunction()));
    EXPECT_TRUE(refused(held, 0ms, clock));
    EXPECT_TRUE(refused(held, -1ms, clock));
    EXPECT_FALSE(refused(held, 1ms, clock));
}
