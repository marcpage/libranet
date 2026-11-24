#include <gtest/gtest.h>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <functional>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "msg/Channel.hpp"
#include "peer/Fetch.hpp"
#include "peer/Seeker.hpp"
#include "stats/Recorder.hpp"
#include "storage/DataNotFound.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/Identifier.hpp"
#include "storage/tests/Fixtures.hpp"

using msg::Channel;
using peer::Fetch;
using peer::Seeker;
using storage::DataNotFound;
using storage::HashPrefix;
using storage::Identifier;
using namespace std::chrono_literals;
using Outcome = Fetch::Outcome;
using SearchRequested = stats::Recorder::SearchRequested;

namespace {

// How long to wait, at most, for what is expected, so that a test fails
// rather than waits forever when it does not come; and how long to wait for
// what is not expected.
constexpr auto patience = std::chrono::seconds(10);
constexpr auto quiet = 300ms;

// How often the seeker looks for passes due, and how far apart a search's
// passes are.
constexpr auto check_interval = 5ms;
constexpr auto pass_interval = 20ms;

// The passes a fetch and a search make.
constexpr std::size_t fetch_passes = 2;
constexpr std::size_t search_passes = 3;

// How many hex digits a SHA-256 hash has.
constexpr std::size_t hash_digits = 64;

Identifier identifier_of(char digit) {
    return {"sha256", std::string(hash_digits, digit)};
}

// Peers that are none, counting how often they are asked for, as a fetch
// asks once a pass and a search once for each time it makes passes. They
// can be held, so that each asking waits until they are let go, as a fetch
// or a search would wait on peers slow to answer.
class CountedPeers {
public:
    [[nodiscard]] std::function<std::vector<Seeker::Session>()> function() {
        return [this] { return _asked(); };
    }

    void hold() {
        const std::scoped_lock lock(m_mutex);
        m_held = true;
    }

    void let_go() {
        {
            const std::scoped_lock lock(m_mutex);
            m_held = false;
        }

        m_changed.notify_all();
    }

    [[nodiscard]] std::size_t asked() const {
        const std::scoped_lock lock(m_mutex);
        return m_asked;
    }

    // Whether, within wait, they have been asked for count times in all.
    [[nodiscard]] bool asked_eventually(std::size_t count,
                                        std::chrono::milliseconds wait = patience) {
        std::unique_lock lock(m_mutex);
        return m_changed.wait_for(lock, wait, [this, count] { return m_asked >= count; });
    }

    // Whether, within patience, count askings wait at once.
    [[nodiscard]] bool waiting_eventually(std::size_t count) {
        std::unique_lock lock(m_mutex);
        return m_changed.wait_for(lock, patience, [this, count] { return m_waiting >= count; });
    }

    [[nodiscard]] std::size_t waiting() const {
        const std::scoped_lock lock(m_mutex);
        return m_waiting;
    }

    CountedPeers() = default;
    ~CountedPeers() = default;

    CountedPeers(const CountedPeers&) = delete;
    CountedPeers& operator=(const CountedPeers&) = delete;
    CountedPeers(CountedPeers&&) = delete;
    CountedPeers& operator=(CountedPeers&&) = delete;

private:
    mutable std::mutex m_mutex;
    std::condition_variable m_changed;
    std::size_t m_asked = 0;   // Guarded by m_mutex
    std::size_t m_waiting = 0; // Guarded by m_mutex
    bool m_held = false;       // Guarded by m_mutex

    // Waits while held, though no longer than patience, so that a test that
    // fails holding them still ends.
    std::vector<Seeker::Session> _asked() {
        std::unique_lock lock(m_mutex);

        ++m_asked;
        ++m_waiting;
        m_changed.notify_all();
        (void)m_changed.wait_for(lock, patience, [this] { return !m_held; });
        --m_waiting;
        m_changed.notify_all();
        return {};
    }
};

// A clock that stands still until a test moves it on, so that a search's
// passes come due only when the test says.
class StillClock {
public:
    [[nodiscard]] Seeker::ClockFunction function() {
        return [this] {
            const std::scoped_lock lock(m_mutex);
            return m_time;
        };
    }

    void move_on(Seeker::Milliseconds interval) {
        const std::scoped_lock lock(m_mutex);
        m_time += interval;
    }

    StillClock() = default;
    ~StillClock() = default;

    StillClock(const StillClock&) = delete;
    StillClock& operator=(const StillClock&) = delete;
    StillClock(StillClock&&) = delete;
    StillClock& operator=(StillClock&&) = delete;

private:
    std::mutex m_mutex;
    Seeker::Clock::time_point m_time = Seeker::Clock::now(); // Guarded by m_mutex
};

// Moves clock on, a pass interval at a time, until peers have been asked for
// count times, and says whether they were within patience. A search asks for
// the peers before it makes the passes due, so a move made before then makes
// those passes due again from the time moved to, and the next move is passed
// over. Each making of passes makes at most one of each search, so moving on
// again never makes more than are due.
bool asked_as_clock_moves(StillClock& clock, CountedPeers& peers, std::size_t count) {
    constexpr auto each_wait = 50ms;
    const auto deadline = std::chrono::steady_clock::now() + patience;

    while (std::chrono::steady_clock::now() < deadline) {
        clock.move_on(pass_interval);

        if (peers.asked_eventually(count, each_wait)) {
            return true;
        }
    }

    return false;
}

// Each test has a fetch, whose peers it counts, and makes a seeker that
// fetches with it, searching peers it counts too.
class SeekerTest : public testing::Test {
protected:
    [[nodiscard]] static Seeker::Settings settings(std::size_t workers = 2) {
        Seeker::Settings made;

        made.workers = workers;
        made.check_interval = check_interval;
        made.search.passes = search_passes;
        made.search.pass_interval = pass_interval;
        made.search.hold = 1h;
        return made;
    }

    [[nodiscard]] std::unique_ptr<Seeker> seeker(std::size_t workers = 2) {
        return std::make_unique<Seeker>(m_fetch, m_searched.function(), settings(workers));
    }

    // A seeker whose searches keep the time clock gives.
    [[nodiscard]] std::unique_ptr<Seeker> seeker_by(StillClock& clock) {
        return std::make_unique<Seeker>(m_fetch, m_searched.function(), settings(),
                                        clock.function());
    }

    [[nodiscard]] CountedPeers& fetched() noexcept {
        return m_fetched;
    }

    [[nodiscard]] CountedPeers& searched() noexcept {
        return m_searched;
    }

    [[nodiscard]] Fetch& fetch() noexcept {
        return m_fetch;
    }

private:
    CountedPeers m_fetched;
    CountedPeers m_searched;
    Fetch m_fetch{std::make_shared<fixtures::MemorySource>(), m_fetched.function(),
                  _fetch_settings()};

    [[nodiscard]] static Fetch::Settings _fetch_settings() {
        Fetch::Settings made;

        made.passes = fetch_passes;
        made.retry_after = 0ms;
        made.failed_search_hold = 1h;
        return made;
    }
};

} // namespace

TEST_F(SeekerTest, FetchesWhatIsNotFound) {
    const auto made = seeker();

    Channel::post(DataNotFound::name, DataNotFound{identifier_of('a')});

    EXPECT_TRUE(fetched().asked_eventually(fetch_passes)) << fetched().asked();
}

TEST_F(SeekerTest, FetchesNoMoreAtOnceThanItHasWorkers) {
    const auto made = seeker(2);

    fetched().hold();
    Channel::post(DataNotFound::name, DataNotFound{identifier_of('a')});
    Channel::post(DataNotFound::name, DataNotFound{identifier_of('b')});
    Channel::post(DataNotFound::name, DataNotFound{identifier_of('c')});

    ASSERT_TRUE(fetched().waiting_eventually(2)) << fetched().waiting();
    std::this_thread::sleep_for(quiet);
    EXPECT_EQ(fetched().waiting(), 2U);

    // Once the two under way end, the third is fetched.
    fetched().let_go();
    EXPECT_TRUE(fetched().asked_eventually(3 * fetch_passes)) << fetched().asked();
}

TEST_F(SeekerTest, ContentWaitingItsTurnIsFetchedOnce) {
    const auto made = seeker(1);

    fetched().hold();
    Channel::post(DataNotFound::name, DataNotFound{identifier_of('a')});
    ASSERT_TRUE(fetched().waiting_eventually(1));
    Channel::post(DataNotFound::name, DataNotFound{identifier_of('b')});
    Channel::post(DataNotFound::name, DataNotFound{identifier_of('b')});
    std::this_thread::sleep_for(quiet);
    fetched().let_go();

    ASSERT_TRUE(fetched().asked_eventually(2 * fetch_passes));
    std::this_thread::sleep_for(quiet);
    EXPECT_EQ(fetched().asked(), 2 * fetch_passes);
}

// Each pass of a search is made as it comes due, and once the last is made,
// the search ends.
TEST_F(SeekerTest, SearchesThePeersForEachSearchRequested) {
    const auto made = seeker();

    Channel::post(SearchRequested::name, SearchRequested{HashPrefix::parse("ab")});

    ASSERT_TRUE(searched().asked_eventually(search_passes)) << searched().asked();
    std::this_thread::sleep_for(quiet);
    EXPECT_EQ(searched().asked(), search_passes);
}

// A search that begins while passes are being made has its first made once
// they are, as they may have passed it over, though no pass has come due
// since. The clock stands still but when the test moves it on, so each
// pass after comes due as the test says, and both searches' passes are made
// together, as they began at one time.
TEST_F(SeekerTest, ASearchRequestedWhilePassesAreMadeIsMadeAfter) {
    StillClock clock;
    const auto made = seeker_by(clock);

    searched().hold();
    Channel::post(SearchRequested::name, SearchRequested{HashPrefix::parse("ab")});
    ASSERT_TRUE(searched().waiting_eventually(1));
    Channel::post(SearchRequested::name, SearchRequested{HashPrefix::parse("cd")});
    std::this_thread::sleep_for(quiet);
    EXPECT_EQ(searched().asked(), 1U);
    searched().let_go();

    // The passes due are made once more at once, and then none until the
    // clock moves on.
    ASSERT_TRUE(searched().asked_eventually(2)) << searched().asked();
    std::this_thread::sleep_for(quiet);
    EXPECT_EQ(searched().asked(), 2U);

    // Then, as the clock moves on, each pass after the first, until the
    // last: one making of passes for each, as each makes at most one pass of
    // each search.
    ASSERT_TRUE(asked_as_clock_moves(clock, searched(), search_passes + 1)) << searched().asked();

    clock.move_on(pass_interval);
    std::this_thread::sleep_for(quiet);
    EXPECT_EQ(searched().asked(), search_passes + 1);
}

// A search the node that asked says may be passed on no more starts none.
TEST_F(SeekerTest, ASearchThatMayBePassedOnNoMoreIsNotMade) {
    const auto made = seeker();

    Channel::post(SearchRequested::name,
                  SearchRequested{HashPrefix::parse("ab"), identifier_of('f'), 0});
    std::this_thread::sleep_for(quiet);

    EXPECT_EQ(searched().asked(), 0U);
}

// The fetches under way end as it goes, and none starts after.
TEST_F(SeekerTest, StoppingStopsTheFetches) {
    auto made = seeker();

    made.reset();

    EXPECT_EQ(fetch().fetch(identifier_of('a')), Outcome::Stopped);
    EXPECT_EQ(fetched().asked(), 0U);
}

TEST_F(SeekerTest, RefusesSettingsItCannotUse) {
    auto no_workers = settings();
    auto no_interval = settings();
    auto one_pass = settings();

    no_workers.workers = 0;
    no_interval.check_interval = 0ms;
    one_pass.search.passes = 1;

    EXPECT_THROW(Seeker(fetch(), searched().function(), no_workers), std::invalid_argument);
    EXPECT_THROW(Seeker(fetch(), searched().function(), no_interval), std::invalid_argument);
    EXPECT_THROW(Seeker(fetch(), searched().function(), one_pass), std::invalid_argument);
    EXPECT_THROW(Seeker(fetch(), nullptr, settings()), std::invalid_argument);
}
