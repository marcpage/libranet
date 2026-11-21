#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <iterator>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "db/Database.hpp"
#include "protocol/NodeList.hpp"
#include "stats/NodeAddresses.hpp"
#include "storage/Identifier.hpp"

using db::Database;
using db::SqliteError;
using protocol::NodeList;
using stats::NodeAddresses;
using storage::Identifier;
using namespace std::chrono_literals;

namespace {

using Address = NodeAddresses::Address;
using Candidate = NodeAddresses::Candidate;
using Clock = NodeAddresses::Clock;
using Limits = NodeAddresses::Limits;
using Source = NodeAddresses::Source;
// An entry of a node list, as endpoint and node id.
using Named = std::pair<std::string, std::string>;
// A candidate, as node id and endpoints.
using Dialable = std::pair<std::string, std::vector<std::string>>;

// SQLite keeps a database of this name in memory, for as long as it is
// open.
constexpr std::string_view in_memory = ":memory:";

// When the tests' clock starts, in whole seconds, as the table keeps times
// to the millisecond.
constexpr auto start = Clock::time_point(1'700'000'000s);

// Endpoints, at addresses set aside for documentation (RFC 5737).
constexpr std::string_view first = "http://203.0.113.1:4300";
constexpr std::string_view second = "http://203.0.113.2:4300";
constexpr std::string_view third = "http://203.0.113.3:4300";
constexpr std::string_view fourth = "http://203.0.113.4:4300";
constexpr std::string_view own_endpoint = "http://198.51.100.7:4300";

// The id of a node whose hash is digit, over and over.
Identifier node(char digit) {
    return {"sha256", std::string(Identifier::longest_hash_digits(), digit)};
}

// This node's own id.
Identifier own_id() {
    return node('0');
}

// A list naming the node of digit at endpoint.
NodeList list_of(std::string_view endpoint, char digit) {
    NodeList list;

    list.add(std::string(endpoint), node(digit));
    return list;
}

// The entry naming the node of digit at endpoint.
Named named(std::string_view endpoint, char digit) {
    return {std::string(endpoint), node(digit).to_string()};
}

// The candidate dialing the node of digit at endpoints.
Dialable dialable(char digit, const std::vector<std::string>& endpoints) {
    return {node(digit).to_string(), endpoints};
}

// The endpoint and node id of each entry of list, in order.
std::vector<Named> entries_of(const NodeList& list) {
    std::vector<Named> entries;

    std::transform(list.entries().begin(), list.entries().end(), std::back_inserter(entries),
                   [](const NodeList::Entry& entry) {
        return Named(entry.endpoint, entry.node_id.to_string());
    });
    return entries;
}

// The endpoint of each of addresses, in order.
std::vector<std::string> endpoints_of(const std::vector<Address>& addresses) {
    std::vector<std::string> endpoints;

    std::transform(addresses.begin(), addresses.end(), std::back_inserter(endpoints),
                   [](const Address& address) { return address.endpoint; });
    return endpoints;
}

// The node id and endpoints of each of candidates, in order.
std::vector<Dialable> dialable_of(const std::vector<Candidate>& candidates) {
    std::vector<Dialable> found;

    std::transform(candidates.begin(), candidates.end(), std::back_inserter(found),
                   [](const Candidate& candidate) {
        return Dialable(candidate.node_id.to_string(), candidate.endpoints);
    });
    return found;
}

// Whether NodeAddresses refuses limits or now as invalid.
bool refused(Database& database, Limits limits, const NodeAddresses::Now& now = &Clock::now) {
    try {
        (void)NodeAddresses(database, limits, now);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

// The addresses, in a database in memory, with a clock that stands still
// until a test moves it on.
class NodeAddressesTest : public testing::Test {
protected:
    NodeAddressesTest();

    // Makes the addresses again, on the same database, with limits.
    void limit(Limits limits);
    void advance(Clock::duration duration) noexcept;

    [[nodiscard]] Database& database() noexcept;
    [[nodiscard]] NodeAddresses& addresses() noexcept;
    // The one address the node of digit has.
    [[nodiscard]] Address only_address(char digit) const;

private:
    Database m_database;
    Clock::time_point m_now = start;
    std::unique_ptr<NodeAddresses> m_addresses;

    [[nodiscard]] NodeAddresses::Now _clock() const;
};

NodeAddressesTest::NodeAddressesTest()
    : m_database(in_memory, NodeAddresses::schema)
    , m_addresses(std::make_unique<NodeAddresses>(m_database, Limits(), _clock())) {}

void NodeAddressesTest::limit(Limits limits) {
    m_addresses = std::make_unique<NodeAddresses>(m_database, limits, _clock());
}

void NodeAddressesTest::advance(Clock::duration duration) noexcept {
    m_now += duration;
}

Database& NodeAddressesTest::database() noexcept {
    return m_database;
}

NodeAddresses& NodeAddressesTest::addresses() noexcept {
    return *m_addresses;
}

Address NodeAddressesTest::only_address(char digit) const {
    const auto found = m_addresses->addresses(node(digit));

    if (1 != found.size()) {
        throw std::logic_error("Expected one address, found " + std::to_string(found.size()));
    }

    return found.front();
}

NodeAddresses::Now NodeAddressesTest::_clock() const {
    return [this] { return m_now; };
}

} // namespace

TEST_F(NodeAddressesTest, AnAddressLearnedIsNotYetTried) {
    addresses().record_learned(list_of(first, 'a'), Source::Relayed);

    const auto address = only_address('a');

    EXPECT_EQ(address.endpoint, first);
    EXPECT_EQ(address.source, Source::Relayed);
    EXPECT_EQ(address.last_learned, start);
    EXPECT_FALSE(address.first_success.has_value());
    EXPECT_FALSE(address.last_success.has_value());
    EXPECT_EQ(address.attempts, 0);
    EXPECT_EQ(address.consecutive_failures, 0);
}

TEST_F(NodeAddressesTest, EachEntryOfAListIsLearned) {
    NodeList list;

    list.add(std::string(first), node('a'));
    list.add(std::string(second), node('b'));
    list.add(std::string(third), node('a'));

    addresses().record_learned(list, Source::Relayed);

    EXPECT_EQ(endpoints_of(addresses().addresses(node('a'))),
              (std::vector<std::string>{std::string(first), std::string(third)}));
    EXPECT_EQ(endpoints_of(addresses().addresses(node('b'))),
              std::vector<std::string>{std::string(second)});
}

TEST_F(NodeAddressesTest, AnAddressLearnedAgainKeepsTheStrongerSource) {
    addresses().record_learned(list_of(first, 'a'), Source::Advertised);
    advance(1s);
    addresses().record_learned(list_of(first, 'a'), Source::Relayed);

    EXPECT_EQ(only_address('a').source, Source::Advertised);
    EXPECT_EQ(only_address('a').last_learned, start + 1s);

    addresses().record_learned(list_of(first, 'a'), Source::Observed);

    EXPECT_EQ(only_address('a').source, Source::Observed);

    addresses().record_success(node('a'), first);
    addresses().record_learned(list_of(first, 'a'), Source::Advertised);

    EXPECT_EQ(only_address('a').source, Source::Dialed);
}

TEST_F(NodeAddressesTest, ASuccessAddsAnAddressDialed) {
    addresses().record_success(node('a'), first);

    const auto address = only_address('a');

    EXPECT_EQ(address.endpoint, first);
    EXPECT_EQ(address.source, Source::Dialed);
    EXPECT_EQ(address.last_learned, start);
    EXPECT_EQ(address.first_success, start);
    EXPECT_EQ(address.last_success, start);
    EXPECT_EQ(address.attempts, 1);
    EXPECT_EQ(address.consecutive_failures, 0);
}

TEST_F(NodeAddressesTest, ASuccessKeepsTheFirstAndEndsTheFailuresInARow) {
    addresses().record_learned(list_of(first, 'a'), Source::Relayed);
    addresses().record_success(node('a'), first);
    advance(1s);
    addresses().record_failure(node('a'), first);
    addresses().record_failure(node('a'), first);
    advance(1s);
    addresses().record_success(node('a'), first);

    const auto address = only_address('a');

    EXPECT_EQ(address.source, Source::Dialed);
    EXPECT_EQ(address.last_learned, start);
    EXPECT_EQ(address.first_success, start);
    EXPECT_EQ(address.last_success, start + 2s);
    EXPECT_EQ(address.attempts, 4);
    EXPECT_EQ(address.consecutive_failures, 0);
}

TEST_F(NodeAddressesTest, AFailureCountsAnAttemptAndAFailureInARow) {
    addresses().record_learned(list_of(first, 'a'), Source::Relayed);
    addresses().record_failure(node('a'), first);
    addresses().record_failure(node('a'), first);

    const auto address = only_address('a');

    EXPECT_FALSE(address.last_success.has_value());
    EXPECT_EQ(address.attempts, 2);
    EXPECT_EQ(address.consecutive_failures, 2);
}

TEST_F(NodeAddressesTest, AFailureAtAnUnknownAddressRecordsNothing) {
    addresses().record_learned(list_of(first, 'a'), Source::Relayed);
    addresses().record_failure(node('a'), second);
    addresses().record_failure(node('b'), first);

    EXPECT_EQ(only_address('a').attempts, 0);
    EXPECT_TRUE(addresses().addresses(node('b')).empty());
}

TEST_F(NodeAddressesTest, AnAddressThatNeverWorkedIsDroppedOnceItFailsTooOften) {
    limit(Limits{2, NodeAddresses::default_max_failures});
    addresses().record_learned(list_of(first, 'a'), Source::Advertised);
    addresses().record_failure(node('a'), first);

    EXPECT_EQ(only_address('a').consecutive_failures, 1);

    addresses().record_failure(node('a'), first);

    EXPECT_TRUE(addresses().addresses(node('a')).empty());
}

TEST_F(NodeAddressesTest, AnAddressThatWorkedIsKeptHoweverOftenItFails) {
    limit(Limits{1, NodeAddresses::default_max_failures});
    addresses().record_success(node('a'), first);
    addresses().record_failure(node('a'), first);
    addresses().record_failure(node('a'), first);
    addresses().record_failure(node('a'), first);

    EXPECT_EQ(only_address('a').consecutive_failures, 3);
}

TEST_F(NodeAddressesTest, AddressesAreInTheOrderTheyAreTried) {
    addresses().record_learned(list_of(first, 'a'), Source::Relayed);
    advance(1s);
    addresses().record_learned(list_of(second, 'a'), Source::Relayed);
    advance(1s);
    addresses().record_learned(list_of(fourth, 'a'), Source::Relayed);
    advance(1s);
    addresses().record_success(node('a'), third);
    advance(1s);
    addresses().record_success(node('a'), first);

    // Those that worked, the most recently first, then the most recently
    // learned.
    EXPECT_EQ(endpoints_of(addresses().addresses(node('a'))),
              (std::vector<std::string>{std::string(first), std::string(third),
                                        std::string(fourth), std::string(second)}));
}

TEST_F(NodeAddressesTest, TheNodeListNamesItsOwnFirstThenTheLastAddressesThatWorked) {
    addresses().record_success(node('a'), first);
    advance(1s);
    addresses().record_success(node('b'), second);
    advance(1s);
    addresses().record_learned(list_of(third, 'c'), Source::Advertised);
    addresses().record_success(node('d'), third);
    advance(1s);
    addresses().record_success(node('d'), fourth);

    const auto list = addresses().node_list(own_id(), {std::string(own_endpoint)});

    // The node never reached is left out, and d is named at its last.
    EXPECT_EQ(entries_of(list),
              (std::vector<Named>{named(own_endpoint, '0'), named(fourth, 'd'),
                                  named(second, 'b'), named(first, 'a')}));
}

TEST_F(NodeAddressesTest, TheNodeListLeavesOutANodeWhoseLastAddressFailedSince) {
    addresses().record_success(node('a'), first);
    addresses().record_success(node('b'), third);
    advance(1s);
    addresses().record_success(node('a'), second);
    addresses().record_failure(node('a'), second);
    addresses().record_failure(node('b'), third);
    addresses().record_success(node('b'), third);

    const auto list = addresses().node_list(own_id(), {std::string(own_endpoint)});

    // a is not named at the first, which it has not been reached at since.
    EXPECT_EQ(entries_of(list),
              (std::vector<Named>{named(own_endpoint, '0'), named(third, 'b')}));
}

TEST_F(NodeAddressesTest, TheNodeListNamesThisNodeOnlyAtItsOwnEndpoints) {
    addresses().record_success(own_id(), first);
    addresses().record_success(node('a'), own_endpoint);

    const auto list = addresses().node_list(own_id(), {std::string(own_endpoint)});

    // An endpoint already named keeps its first entry.
    EXPECT_EQ(entries_of(list), std::vector<Named>{named(own_endpoint, '0')});
}

TEST_F(NodeAddressesTest, CandidatesReachedComeFirstThenTheMostRecentlyLearned) {
    addresses().record_learned(list_of(first, 'a'), Source::Relayed);
    advance(1s);
    addresses().record_success(node('b'), second);
    advance(1s);
    addresses().record_learned(list_of(third, 'c'), Source::Relayed);
    addresses().record_learned(list_of(fourth, 'b'), Source::Relayed);
    advance(1s);
    addresses().record_learned(list_of(own_endpoint, '0'), Source::Relayed);

    EXPECT_EQ(dialable_of(addresses().candidates(own_id())),
              (std::vector<Dialable>{
                  dialable('b', {std::string(second), std::string(fourth)}),
                  dialable('c', {std::string(third)}),
                  dialable('a', {std::string(first)}),
              }));
}

TEST_F(NodeAddressesTest, ANodeIsNoLongerDialedOnceEachAddressFailedTooOften) {
    limit(Limits{NodeAddresses::default_max_failures, 2});
    addresses().record_learned(list_of(first, 'a'), Source::Relayed);
    addresses().record_learned(list_of(second, 'a'), Source::Relayed);
    addresses().record_success(node('b'), third);

    const std::vector<std::pair<char, std::string_view>> failures = {
        {'a', first},
        {'a', first},
        {'a', second},
        {'b', third},
        {'b', third},
    };

    for (const auto& [digit, endpoint] : failures) {
        addresses().record_failure(node(digit), endpoint);
    }

    // a has an address that has failed only once.
    EXPECT_EQ(dialable_of(addresses().candidates(own_id())),
              (std::vector<Dialable>{dialable('a', {std::string(first), std::string(second)})}));

    addresses().record_failure(node('a'), second);

    EXPECT_TRUE(addresses().candidates(own_id()).empty());
    EXPECT_EQ(addresses().addresses(node('a')).size(), 2U);
}

TEST_F(NodeAddressesTest, LimitsLessThanOneAreRefused) {
    EXPECT_TRUE(refused(database(), Limits{0, 1}));
    EXPECT_TRUE(refused(database(), Limits{1, 0}));
    EXPECT_TRUE(refused(database(), Limits{-1, 1}));
    EXPECT_FALSE(refused(database(), Limits{1, 1}));
}

TEST_F(NodeAddressesTest, AClockIsNeeded) {
    EXPECT_TRUE(refused(database(), Limits(), NodeAddresses::Now()));
}

TEST_F(NodeAddressesTest, ASuccessAtWhatIsNoEndpointIsRefused) {
    EXPECT_THROW(addresses().record_success(node('a'), "203.0.113.1:4300"),
                 std::invalid_argument);
    EXPECT_THROW(addresses().record_success(node('a'), "http://203.0.113.1:4300/data"),
                 std::invalid_argument);
    EXPECT_TRUE(addresses().addresses(node('a')).empty());
}

TEST_F(NodeAddressesTest, TheTableIsNeeded) {
    Database other(in_memory, "");
    NodeAddresses elsewhere(other, Limits());

    EXPECT_THROW(elsewhere.record_learned(list_of(first, 'a'), Source::Relayed), SqliteError);
    EXPECT_THROW((void)elsewhere.candidates(own_id()), SqliteError);
}

TEST_F(NodeAddressesTest, ARowOfAnUnknownSourceIsRefused) {
    addresses().record_learned(list_of(first, 'a'), Source::Relayed);
    database().execute("UPDATE node_addresses SET source = 'rumored'");

    EXPECT_THROW((void)addresses().addresses(node('a')), std::invalid_argument);
}
