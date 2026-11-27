#include <gtest/gtest.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <iterator>
#include <memory>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "eviction/Evictor.hpp"
#include "msg/Channel.hpp"
#include "msg/Message.hpp"
#include "storage/DataDeleted.hpp"
#include "storage/DataStored.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "storage/tests/Fixtures.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"

using eviction::Evictor;
using fixtures::MemorySource;
using fixtures::ScratchDirectory;
using msg::Channel;
using storage::DataDeleted;
using storage::DataStored;
using storage::HashPrefix;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;
using types::Megabyte;
using namespace std::chrono_literals;

// The tests only take what the evictor posts, from another file, so
// nothing here would make their messages' virtual to_string(), which
// next<>() casts to.
template class msg::Message<DataDeleted>;
template class msg::Message<Evictor::CandidatesRequested>;
template class msg::Message<Evictor::HandedOff>;

namespace {

using Candidates = Evictor::Candidates;
using CandidatesRequested = Evictor::CandidatesRequested;
using HandedOff = Evictor::HandedOff;
using Held = Evictor::Held;
using Settings = Evictor::Settings;
using Texts = std::vector<std::string>;

// How long a test waits for what the evictor posts. A wait ends as soon as
// it is posted, so only a failing test waits this long; it is generous
// because a sanitizer build, sharing a slow machine with other tests, can
// take far longer than a test run alone.
constexpr auto post_timeout = 5s;

// How long a test watches for what should not happen.
constexpr auto quiet_time = 50ms;

// How long what is stored is kept, and how long a request for a list goes
// unanswered before it may be asked again.
constexpr auto stored_grace = 10s;
constexpr auto candidates_timeout = 20s;

// The most the content held may take. One hand-off at a time aims 1 MiB
// short of it.
constexpr std::uintmax_t limit = 4 * Megabyte;

// How large the content the tests store is, as stored: with 1 MiB of
// headroom, three is as much as may be held, and one more is 1 MiB over.
constexpr std::size_t mebibyte = Megabyte;
constexpr std::size_t three_mebibytes = 3 * Megabyte;

// When the tests' clock starts.
constexpr auto start = Evictor::Clock::time_point(1'000'000s);

// The id of a node, or the identifier of content, whose hash is digit, over
// and over.
Identifier named(char digit) {
    return {"sha256", std::string(Identifier::longest_hash_digits(), digit)};
}

// This node's id.
Identifier own_id() {
    return named('0');
}

// The text of each of identifiers, in order.
Texts texts_of(const std::vector<Identifier>& identifiers) {
    Texts texts;

    std::transform(identifiers.begin(), identifiers.end(), std::back_inserter(texts),
                   [](const Identifier& identifier) { return identifier.to_string(); });
    return texts;
}

// The text of the identifier of each digit, in order.
Texts texts_of(const std::string& digits) {
    Texts texts;

    std::transform(digits.begin(), digits.end(), std::back_inserter(texts),
                   [](char digit) { return named(digit).to_string(); });
    return texts;
}

// The settings the tests start with: 4 MiB at most, held one hand-off at a
// time, with free space not limited.
Settings test_settings() {
    Settings settings;

    settings.max_storage_bytes = limit;
    settings.min_free_bytes = 0;
    settings.max_hand_offs = 1;
    settings.stored_grace = stored_grace;
    settings.candidates_timeout = candidates_timeout;
    return settings;
}

// A store that keeps what it is given in memory, for the evictor's thread
// and the test's at once.
class SharedSource : public IDataSource {
public:
    [[nodiscard]] bool has(const Identifier& identifier) const override;
    [[nodiscard]] std::optional<Data> get(const Identifier& identifier) const override;
    void put(const Identifier& identifier, const Data& body) const override;
    [[nodiscard]] bool remove(const Identifier& identifier) const override;
    [[nodiscard]] std::uintmax_t size() const override;
    [[nodiscard]] Identifiers starting_with(const HashPrefix& prefix) const override;

private:
    mutable std::mutex m_mutex;
    MemorySource m_source; // Guarded by m_mutex
};

bool SharedSource::has(const Identifier& identifier) const {
    const std::scoped_lock lock(m_mutex);

    return m_source.has(identifier);
}

std::optional<IDataSource::Data> SharedSource::get(const Identifier& identifier) const {
    const std::scoped_lock lock(m_mutex);

    return m_source.get(identifier);
}

void SharedSource::put(const Identifier& identifier, const Data& body) const {
    const std::scoped_lock lock(m_mutex);

    m_source.put(identifier, body);
}

bool SharedSource::remove(const Identifier& identifier) const {
    const std::scoped_lock lock(m_mutex);

    return m_source.remove(identifier);
}

std::uintmax_t SharedSource::size() const {
    const std::scoped_lock lock(m_mutex);

    return m_source.size();
}

IDataSource::Identifiers SharedSource::starting_with(const HashPrefix& prefix) const {
    const std::scoped_lock lock(m_mutex);

    return m_source.starting_with(prefix);
}

// The peers, as a test sets them: which one takes what is offered, if any,
// which are connected, and whether hand-offs wait until the test lets them
// go on. It keeps what was offered.
class StandInPeers {
public:
    // What the evictor is given, which uses this, so this must outlive it.
    [[nodiscard]] Evictor::Peers for_evictor();
    void set_taker(std::optional<Identifier> new_taker);
    void set_connected(std::vector<Identifier> new_connected);
    // Has each hand-off throw.
    void fail();
    // Has each hand-off wait, until let_go().
    void hold();
    void let_go();
    // What was offered, sorted, once count were, or as many as were in
    // time. Hand-offs under way at once are offered in any order.
    [[nodiscard]] Texts offered(std::size_t count = 0);

private:
    std::mutex m_mutex;
    std::condition_variable m_changed;
    std::optional<Identifier> m_taker;   // Guarded by m_mutex
    std::vector<Identifier> m_connected; // Guarded by m_mutex
    bool m_failing = false;              // Guarded by m_mutex
    bool m_holding = false;              // Guarded by m_mutex
    Texts m_offered;                     // Guarded by m_mutex

    [[nodiscard]] std::optional<Identifier> _hand_off(const Identifier& identifier);
};

Evictor::Peers StandInPeers::for_evictor() {
    return {
        [this](const Identifier& identifier) { return _hand_off(identifier); },
        [this] {
        const std::scoped_lock lock(m_mutex);

        return m_connected;
    },
    };
}

void StandInPeers::set_taker(std::optional<Identifier> new_taker) {
    const std::scoped_lock lock(m_mutex);

    m_taker = std::move(new_taker);
}

void StandInPeers::set_connected(std::vector<Identifier> new_connected) {
    const std::scoped_lock lock(m_mutex);

    m_connected = std::move(new_connected);
}

void StandInPeers::fail() {
    const std::scoped_lock lock(m_mutex);

    m_failing = true;
}

void StandInPeers::hold() {
    const std::scoped_lock lock(m_mutex);

    m_holding = true;
}

void StandInPeers::let_go() {
    {
        const std::scoped_lock lock(m_mutex);

        m_holding = false;
    }

    m_changed.notify_all();
}

Texts StandInPeers::offered(std::size_t count) {
    std::unique_lock lock(m_mutex);

    (void)m_changed.wait_for(lock, post_timeout,
                             [this, count] { return m_offered.size() >= count; });

    auto offered = m_offered;

    std::sort(offered.begin(), offered.end());
    return offered;
}

std::optional<Identifier> StandInPeers::_hand_off(const Identifier& identifier) {
    std::unique_lock lock(m_mutex);

    m_offered.push_back(identifier.to_string());
    m_changed.notify_all();
    m_changed.wait(lock, [this] { return !m_holding; });

    if (m_failing) {
        throw std::runtime_error("The peers could not be asked");
    }

    return m_taker;
}

// An evictor of the test's settings, with a store, peers, free space, and a
// clock of the test's own, and channels that take what it posts, made
// before it so that they take all of it.
class EvictorTest : public testing::Test {
protected:
    void TearDown() override;
    // Starts the evictor, with settings.
    void start_evicting(const Settings& settings = test_settings());
    // Puts the content of digit in the store, without posting it.
    void hold(char digit);
    // Puts the content of digit in the store, and posts it as stored,
    // taking size bytes.
    void store(char digit, std::size_t size);
    // Starts the evictor with settings, holding the content of a, 3 MiB,
    // which it counts as it starts; then stores the content of b, 1 MiB,
    // which is 1 MiB over what the test settings hold, with the headroom.
    void go_over(const Settings& settings = test_settings());
    // Answers with objects, as stats::Recorder would, each put in the store.
    void answer(const std::vector<Held>& objects) const;
    // Moves the clock on, which the evictor's thread reads.
    void advance(std::chrono::milliseconds duration) noexcept;
    // The next request, hand-off, or deletion posted. Throws
    // std::runtime_error if none is posted in time.
    [[nodiscard]] CandidatesRequested next_request();
    [[nodiscard]] HandedOff next_handed_off();
    [[nodiscard]] DataDeleted next_deleted();
    // Whether no request, or no deletion, is posted for a while.
    [[nodiscard]] bool no_request();
    [[nodiscard]] bool none_deleted();
    // The store, the peers, and the bytes free, that the evictor is given.
    [[nodiscard]] SharedSource& store() noexcept;
    [[nodiscard]] StandInPeers& stand_in() noexcept;
    void set_free_bytes(std::uintmax_t new_free_bytes) noexcept;

private:
    std::shared_ptr<SharedSource> m_store = std::make_shared<SharedSource>();
    StandInPeers m_peers;
    std::atomic<std::uintmax_t> m_free_bytes{0};
    Channel m_requests{CandidatesRequested::name};
    Channel m_handed_off{HandedOff::name};
    Channel m_deleted{DataDeleted::name};
    std::atomic<std::int64_t> m_elapsed_milliseconds{0};
    std::unique_ptr<Evictor> m_evictor;
};

// The hand-offs under way end before the evictor waits for them.
void EvictorTest::TearDown() {
    m_peers.let_go();
    m_evictor.reset();
}

void EvictorTest::start_evicting(const Settings& settings) {
    m_evictor = std::make_unique<Evictor>(
        own_id(), m_store, m_peers.for_evictor(), [this] { return m_free_bytes.load(); }, settings,
        [this] { return start + std::chrono::milliseconds(m_elapsed_milliseconds); });
}

void EvictorTest::hold(char digit) {
    m_store->put(named(digit), Converter::bytes_of(std::string(1, digit)));
}

void EvictorTest::store(char digit, std::size_t size) {
    hold(digit);
    Channel::post(DataStored::name, DataStored{named(digit), std::nullopt, size});
}

void EvictorTest::go_over(const Settings& settings) {
    m_store->put(named('a'), IDataSource::Data(three_mebibytes));
    start_evicting(settings);
    store('b', mebibyte);
}

void EvictorTest::answer(const std::vector<Held>& objects) const {
    for (const auto& object : objects) {
        m_store->put(object.identifier, Converter::bytes_of("content"));
    }

    Channel::post(Candidates::name, Candidates{objects});
}

void EvictorTest::advance(std::chrono::milliseconds duration) noexcept {
    m_elapsed_milliseconds += duration.count();
}

CandidatesRequested EvictorTest::next_request() {
    auto posted = m_requests.next<CandidatesRequested>(post_timeout);

    if (!posted) {
        throw std::runtime_error("No request was posted");
    }

    return std::move(*posted);
}

HandedOff EvictorTest::next_handed_off() {
    auto posted = m_handed_off.next<HandedOff>(post_timeout);

    if (!posted) {
        throw std::runtime_error("No hand-off ended");
    }

    return std::move(*posted);
}

DataDeleted EvictorTest::next_deleted() {
    auto posted = m_deleted.next<DataDeleted>(post_timeout);

    if (!posted) {
        throw std::runtime_error("Nothing was deleted");
    }

    return std::move(*posted);
}

bool EvictorTest::no_request() {
    return !m_requests.next<CandidatesRequested>(quiet_time);
}

bool EvictorTest::none_deleted() {
    return !m_deleted.next<DataDeleted>(quiet_time);
}

SharedSource& EvictorTest::store() noexcept {
    return *m_store;
}

StandInPeers& EvictorTest::stand_in() noexcept {
    return m_peers;
}

void EvictorTest::set_free_bytes(std::uintmax_t new_free_bytes) noexcept {
    m_free_bytes = new_free_bytes;
}

// Whether an Evictor refuses settings, or what it is given, as invalid.
bool refused(const Settings& settings, IDataSource::Ptr store = std::make_shared<SharedSource>(),
             Evictor::Peers peers = {
                 [](const Identifier&) { return std::nullopt; },
                 [] { return std::vector<Identifier>(); },
             },
             Evictor::FreeSpace free_space = [] { return std::uintmax_t{0}; },
             Evictor::ClockFunction clock = &Evictor::Clock::now) {
    try {
        (void)Evictor(own_id(), std::move(store), std::move(peers), std::move(free_space),
                      settings, std::move(clock));
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

} // namespace

TEST_F(EvictorTest, WithinItsLimitsItAsksForNothing) {
    start_evicting();
    store('a', three_mebibytes);

    EXPECT_TRUE(no_request());
}

TEST_F(EvictorTest, OverItsLimitItAsksForWhatToLetGoOfFirst) {
    stand_in().set_connected({named('9')});
    go_over();

    const auto request = next_request();

    EXPECT_EQ(request.bytes, Megabyte);
    EXPECT_EQ(texts_of(request.exclude), texts_of("90b"));
}

TEST_F(EvictorTest, TooLittleFreeSpaceIsLetGoOfToo) {
    constexpr std::uintmax_t min_free_bytes = 10 * Megabyte;
    constexpr std::uintmax_t free_bytes = 8 * Megabyte;
    auto settings = test_settings();

    settings.max_storage_bytes = std::nullopt;
    settings.min_free_bytes = min_free_bytes;
    set_free_bytes(free_bytes);
    start_evicting(settings);
    store('a', 1);

    EXPECT_EQ(next_request().bytes, 3 * Megabyte);
}

TEST_F(EvictorTest, WhatIsHeldIsCountedAsItStarts) {
    store().put(named('a'), IDataSource::Data(limit));
    start_evicting();
    store('b', 0);

    EXPECT_EQ(next_request().bytes, Megabyte);
}

TEST_F(EvictorTest, WhatAPeerTookIsDeleted) {
    stand_in().set_taker(named('9'));
    go_over();
    (void)next_request();
    answer({{named('c'), three_mebibytes}});

    const auto deleted = next_deleted();

    EXPECT_EQ(deleted.identifier.to_string(), named('c').to_string());
    EXPECT_EQ(deleted.size, three_mebibytes);
    EXPECT_FALSE(store().has(named('c')));
    EXPECT_EQ(stand_in().offered(), texts_of("c"));
}

TEST_F(EvictorTest, WhatNoPeerTookIsKeptAndHoldsOffMoreUntilTheNextStore) {
    go_over();
    (void)next_request();
    answer({{named('c'), mebibyte}, {named('d'), mebibyte}});

    EXPECT_FALSE(next_handed_off().holder.has_value());
    EXPECT_TRUE(none_deleted());
    EXPECT_TRUE(store().has(named('c')));
    EXPECT_EQ(stand_in().offered(), texts_of("c"));

    store('e', 1);

    EXPECT_EQ(stand_in().offered(2), texts_of("cd"));
}

// With two at once, 2 MiB of headroom: a list that comes while hand-offs are
// held off waits for the next store too.
TEST_F(EvictorTest, AListThatComesWhileHeldOffWaitsForTheNextStore) {
    auto settings = test_settings();

    settings.max_hand_offs = 2;
    stand_in().hold();
    go_over(settings);
    (void)next_request();
    answer({{named('c'), 1}});
    (void)stand_in().offered(1);
    store('d', 1);

    EXPECT_EQ(texts_of(next_request().exclude), texts_of("0cbd"));

    stand_in().let_go();
    (void)next_handed_off();
    answer({{named('e'), mebibyte}});

    EXPECT_TRUE(none_deleted());
    EXPECT_EQ(stand_in().offered(), texts_of("c"));

    store('f', 1);

    EXPECT_EQ(stand_in().offered(2), texts_of("ce"));
}

TEST_F(EvictorTest, ContentGoneWhenAPeerTookItIsPostedAsDeleted) {
    stand_in().set_taker(named('9'));
    stand_in().hold();
    go_over();
    (void)next_request();
    answer({{named('c'), mebibyte}});
    (void)stand_in().offered(1);
    (void)store().remove(named('c'));
    stand_in().let_go();

    EXPECT_EQ(next_deleted().identifier.to_string(), named('c').to_string());
}

TEST_F(EvictorTest, AHandOffThatFailsTookItToNoPeer) {
    stand_in().fail();
    go_over();
    (void)next_request();
    answer({{named('c'), mebibyte}});

    EXPECT_FALSE(next_handed_off().holder.has_value());
    EXPECT_TRUE(none_deleted());
    EXPECT_TRUE(store().has(named('c')));
}

TEST_F(EvictorTest, KeysAndWhatIsWithinItsGraceArePassedOver) {
    stand_in().set_connected({named('9')});
    stand_in().set_taker(named('8'));
    go_over();
    (void)next_request();
    answer({
        {own_id(), mebibyte},
        {named('9'), mebibyte},
        {named('b'), mebibyte},
        {named('c'), mebibyte},
    });

    EXPECT_EQ(next_deleted().identifier.to_string(), named('c').to_string());
    EXPECT_EQ(stand_in().offered(), texts_of("c"));
    EXPECT_TRUE(store().has(own_id()));
    EXPECT_TRUE(store().has(named('9')));
    EXPECT_TRUE(store().has(named('b')));
}

TEST_F(EvictorTest, WhatWasStoredIsLetGoOfOnceItsGraceIsOver) {
    stand_in().set_taker(named('9'));
    go_over();
    (void)next_request();
    advance(stored_grace);
    answer({{named('b'), mebibyte}});

    EXPECT_EQ(next_deleted().identifier.to_string(), named('b').to_string());
}

TEST_F(EvictorTest, AKeyConnectedMeanwhileIsNotDeleted) {
    stand_in().set_taker(named('8'));
    stand_in().hold();
    go_over();
    (void)next_request();
    answer({{named('c'), mebibyte}});
    (void)stand_in().offered(1);
    stand_in().set_connected({named('c')});
    stand_in().let_go();

    EXPECT_EQ(next_handed_off().holder.value_or(own_id()).to_string(), named('8').to_string());
    EXPECT_TRUE(none_deleted());
    EXPECT_TRUE(store().has(named('c')));
}

// With two at once, 2 MiB of headroom: 5 MiB held is 3 MiB over, which
// three of the objects listed free.
TEST_F(EvictorTest, NoMoreAreHandedOffAtOnceThanMayBeNorMoreThanAreNeeded) {
    constexpr std::size_t five_mebibytes = 5 * Megabyte;
    auto settings = test_settings();

    settings.max_hand_offs = 2;
    stand_in().set_taker(named('9'));
    stand_in().hold();
    start_evicting(settings);
    store('a', five_mebibytes);
    (void)next_request();
    answer({
        {named('c'), mebibyte},
        {named('d'), mebibyte},
        {named('e'), mebibyte},
        {named('f'), mebibyte},
    });

    EXPECT_EQ(stand_in().offered(2), texts_of("cd"));
    EXPECT_TRUE(none_deleted());
    EXPECT_EQ(stand_in().offered(), texts_of("cd"));

    stand_in().let_go();

    EXPECT_EQ(stand_in().offered(3), texts_of("cde"));
    (void)next_deleted();
    (void)next_deleted();
    (void)next_deleted();
    EXPECT_TRUE(none_deleted());
    EXPECT_EQ(stand_in().offered(), texts_of("cde"));
}

TEST_F(EvictorTest, ItAsksAgainOnceTheListRunsOut) {
    stand_in().set_taker(named('9'));
    go_over();
    (void)next_request();
    answer({{named('c'), 1}});
    (void)next_deleted();

    EXPECT_EQ(next_request().bytes, Megabyte - 1);
}

TEST_F(EvictorTest, AnEmptyListWaitsForTheNextStore) {
    go_over();
    (void)next_request();
    answer({});

    EXPECT_TRUE(no_request());

    store('c', 1);

    EXPECT_EQ(next_request().bytes, Megabyte + 1);
}

TEST_F(EvictorTest, AListAskedForIsAskedForAgainOnlyOnceOverdue) {
    go_over();
    (void)next_request();
    store('c', 1);

    EXPECT_TRUE(no_request());

    advance(candidates_timeout);
    store('d', 1);

    EXPECT_NO_THROW((void)next_request());
}

TEST_F(EvictorTest, WhatIsNoLongerHeldIsPostedAsDeleted) {
    go_over();
    (void)next_request();
    Channel::post(Candidates::name, Candidates{{{named('c'), mebibyte}}});

    const auto deleted = next_deleted();

    EXPECT_EQ(deleted.identifier.to_string(), named('c').to_string());
    EXPECT_EQ(deleted.size, mebibyte);
    EXPECT_TRUE(stand_in().offered().empty());
}

TEST_F(EvictorTest, AnEventOfAnotherKindUnderItsNamesIsLetGo) {
    start_evicting();
    Channel::post(DataStored::name, std::string("not content stored"));
    store('a', three_mebibytes);
    store('b', mebibyte);

    EXPECT_EQ(next_request().bytes, Megabyte);
}

TEST(EvictorSettingsTest, WhatItCannotUseIsRefused) {
    auto settings = test_settings();

    EXPECT_FALSE(refused(settings));
    settings.max_hand_offs = 0;
    EXPECT_TRUE(refused(settings));
    settings = test_settings();
    settings.stored_grace = -1ms;
    EXPECT_TRUE(refused(settings));
    settings = test_settings();
    settings.candidates_timeout = 0ms;
    EXPECT_TRUE(refused(settings));
}

TEST(EvictorSettingsTest, WhatItIsGivenMustBeThere) {
    const auto none = [](const Identifier&) { return std::optional<Identifier>(); };
    const auto nobody = [] { return std::vector<Identifier>(); };

    EXPECT_TRUE(refused(test_settings(), nullptr));
    EXPECT_TRUE(refused(test_settings(), std::make_shared<SharedSource>(), {nullptr, nobody}));
    EXPECT_TRUE(refused(test_settings(), std::make_shared<SharedSource>(), {none, nullptr}));
    EXPECT_TRUE(
        refused(test_settings(), std::make_shared<SharedSource>(), {none, nobody}, nullptr));
    EXPECT_TRUE(refused(test_settings(), std::make_shared<SharedSource>(), {none, nobody},
                        [] { return std::uintmax_t{0}; }, nullptr));
}

TEST(EvictorFreeSpaceTest, IsWhatTheFileSystemHasFree) {
    const ScratchDirectory directory;
    const auto free_space = Evictor::free_space_in(directory.path());

    EXPECT_GT(free_space(), 0U);
    EXPECT_THROW((void)Evictor::free_space_in(directory.path() / "missing")(),
                 std::filesystem::filesystem_error);
}

TEST(EvictorEventTest, EachEventSaysWhatItHolds) {
    constexpr std::uintmax_t bytes = 5;

    EXPECT_EQ(std::string(CandidatesRequested{bytes, {named('a')}}), "5 bytes, 1 left out");
    EXPECT_EQ(std::string(Candidates{{{named('a'), 1}, {named('b'), 2}}}), "2 objects");
    EXPECT_EQ(std::string(HandedOff{{named('a'), 1}, named('9')}),
              named('a').to_string() + " taken by " + named('9').to_string());
    EXPECT_EQ(std::string(HandedOff{{named('a'), 1}, std::nullopt}),
              named('a').to_string() + " taken by no peer");
}
