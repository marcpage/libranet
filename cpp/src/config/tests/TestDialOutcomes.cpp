#include <gtest/gtest.h>
#include <algorithm>
#include <chrono>
#include <cstddef>
#include <future>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "config/DialOutcomes.hpp"
#include "logger/ILogOutput.hpp"
#include "logger/Logger.hpp"
#include "msg/Channel.hpp"
#include "peer/PeerConnection.hpp"
#include "storage/Identifier.hpp"

using config::DialOutcomes;
using logger::ILogOutput;
using logger::Logger;
using msg::Channel;
using peer::PeerConnection;
using std::async;
using std::future;
using std::future_status;
using std::launch;
using std::make_shared;
using std::make_unique;
using std::mutex;
using std::optional;
using std::promise;
using std::runtime_error;
using std::scoped_lock;
using std::set;
using std::shared_ptr;
using std::string;
using std::string_view;
using std::vector;
using storage::Identifier;
using namespace std::chrono_literals;
using AddressDialed = DialOutcomes::AddressDialed;
using Milliseconds = DialOutcomes::Milliseconds;
using Outcome = AddressDialed::Outcome;

namespace {

// Far longer than anything here takes, and far shorter than a request
// waits with the Python node's timeouts.
constexpr Milliseconds patience = 5s;

// With brief(), a request waits 1 + 2 * 2 + 5 ms.
constexpr Milliseconds brief_report = 5ms;

// Timeouts a dial is over well within here.
PeerConnection::Limits brief() {
    return {1ms, 2ms, {}};
}

// The Python node's, as peer::Connections is given them by default.
PeerConnection::Limits python_limits() {
    return {};
}

// A node reached, as a dial's outcome names it.
Identifier node_reached() {
    return Identifier::of({std::byte{'n'}, std::byte{'o'}, std::byte{'d'}, std::byte{'e'}});
}

// What peer::Connections posts once the dial dial_id names is over.
void post_outcome(const string& dial_id, Outcome outcome,
                  optional<Identifier> node_id = std::nullopt, string detail = "") {
    Channel::post(AddressDialed::name,
                  AddressDialed{dial_id, outcome, std::move(node_id), std::move(detail)});
}

// Whether dial_id is 16 random bytes in hex, as the Python node's are.
bool is_dial_id(const string& dial_id) {
    constexpr std::size_t hex_digits = 32;

    return hex_digits == dial_id.size()
           && string::npos == dial_id.find_first_not_of("0123456789abcdef");
}

// Keeps the id of the dial it is to ask for in asked, and fails to ask.
DialOutcomes::Ask failing_ask(string& asked) {
    return [&asked](const string& dial_id) {
        asked = dial_id;
        throw runtime_error("could not ask");
    };
}

// What came of a dial outcomes is asked to wait on, asked from a thread of
// its own. Each dial's id is handed to asked once it is asked for, and the
// dial is otherwise left for the test to answer.
future<optional<AddressDialed>> await_on(DialOutcomes& outcomes, promise<string>& asked) {
    return async(launch::async, [&outcomes, &asked] {
        return outcomes.await([&asked](const string& dial_id) { asked.set_value(dial_id); });
    });
}

// Keeps each line it is given, where a test can read them while the Logger
// owns it.
class KeepingOutput : public ILogOutput {
public:
    using Lines = vector<string>;

    explicit KeepingOutput(shared_ptr<Lines> lines, shared_ptr<mutex> guard)
        : m_lines(std::move(lines))
        , m_guard(std::move(guard)) {}

    void log(string_view line) override {
        const scoped_lock lock(*m_guard);

        m_lines->emplace_back(line);
    }

private:
    shared_ptr<Lines> m_lines;
    shared_ptr<mutex> m_guard;
};

// Each test keeps what is logged.
class DialOutcomesTest : public testing::Test {
protected:
    void SetUp() override {
        Logger::remove_all_outputs();
        Logger::add_output(make_unique<KeepingOutput>(m_lines, m_guard));
        Logger::reset_levels(Logger::Level::Info);
    }

    void TearDown() override {
        Logger::remove_all_outputs();
    }

    // Whether a line logged holds text.
    [[nodiscard]] bool logged(string_view text) const {
        const scoped_lock lock(*m_guard);

        return std::any_of(m_lines->begin(), m_lines->end(), [text](const string& line) {
            return string::npos != line.find(text);
        });
    }

private:
    shared_ptr<KeepingOutput::Lines> m_lines = make_shared<KeepingOutput::Lines>();
    shared_ptr<mutex> m_guard = make_shared<mutex>();
};

} // namespace

// The Python node's: 10 seconds to connect, two requests of 30, and 5 more.
TEST_F(DialOutcomesTest, RequestWaitsAsLongAsOneDialMayTakeAndFiveSecondsMore) {
    const DialOutcomes outcomes(python_limits());

    EXPECT_EQ(DialOutcomes::default_report_time, 5s);
    EXPECT_EQ(outcomes.wait(), 75s);
}

TEST_F(DialOutcomesTest, RequestWaitsAsLongAsTheTimeoutsGiven) {
    const DialOutcomes outcomes({3s, 7s, {}}, 1s);

    EXPECT_EQ(outcomes.wait(), 18s);
}

TEST_F(DialOutcomesTest, OutcomeIsHandedToTheRequestWaitingForIt) {
    DialOutcomes outcomes(python_limits());
    string asked;

    const auto dialed = outcomes.await([&asked](const string& dial_id) {
        asked = dial_id;
        post_outcome(dial_id, Outcome::Connected, node_reached());
    });

    ASSERT_TRUE(dialed.has_value());
    EXPECT_EQ(dialed->dial_id, asked);
    EXPECT_EQ(dialed->outcome, Outcome::Connected);
    ASSERT_TRUE(dialed->node_id.has_value());
    EXPECT_EQ(dialed->node_id->to_string(), node_reached().to_string());
    EXPECT_TRUE(dialed->detail.empty());
}

TEST_F(DialOutcomesTest, OutcomeNamingNoNodeIsHandedOn) {
    DialOutcomes outcomes(python_limits());

    const auto dialed = outcomes.await([](const string& dial_id) {
        post_outcome(dial_id, Outcome::Unreached, std::nullopt, "connection refused");
    });

    ASSERT_TRUE(dialed.has_value());
    EXPECT_EQ(dialed->outcome, Outcome::Unreached);
    EXPECT_FALSE(dialed->node_id.has_value());
    EXPECT_EQ(dialed->detail, "connection refused");
}

TEST_F(DialOutcomesTest, EachRequestIsGivenADialIdOfItsOwn) {
    DialOutcomes outcomes(brief(), brief_report);
    vector<string> asked;

    for (auto count = 0; count < 3; ++count) {
        (void)outcomes.await([&asked](const string& dial_id) { asked.push_back(dial_id); });
    }

    EXPECT_TRUE(std::all_of(asked.begin(), asked.end(), is_dial_id));
    EXPECT_EQ(set<string>(asked.begin(), asked.end()).size(), 3U);
}

// Two requests for one address are each answered, whichever dial is over
// first.
TEST_F(DialOutcomesTest, RequestsWaitingAtOnceAreEachHandedTheirOwn) {
    DialOutcomes outcomes(python_limits());
    promise<string> first_asked;
    promise<string> second_asked;
    auto first = await_on(outcomes, first_asked);
    auto second = await_on(outcomes, second_asked);
    const auto first_id = first_asked.get_future().get();
    const auto second_id = second_asked.get_future().get();

    post_outcome(second_id, Outcome::AlreadyConnected, node_reached());
    post_outcome(first_id, Outcome::Unreached, std::nullopt, "first");

    ASSERT_EQ(first.wait_for(patience), future_status::ready);
    ASSERT_EQ(second.wait_for(patience), future_status::ready);

    const auto first_dialed = first.get();
    const auto second_dialed = second.get();

    ASSERT_TRUE(first_dialed.has_value());
    EXPECT_EQ(first_dialed->dial_id, first_id);
    EXPECT_EQ(first_dialed->outcome, Outcome::Unreached);
    EXPECT_EQ(first_dialed->detail, "first");
    ASSERT_TRUE(second_dialed.has_value());
    EXPECT_EQ(second_dialed->dial_id, second_id);
    EXPECT_EQ(second_dialed->outcome, Outcome::AlreadyConnected);
}

TEST_F(DialOutcomesTest, NothingComingInTimeIsNothing) {
    DialOutcomes outcomes(brief(), brief_report);
    auto asked = false;
    const auto started = std::chrono::steady_clock::now();

    const auto dialed = outcomes.await([&asked](const string& /*dial_id*/) { asked = true; });

    EXPECT_TRUE(asked);
    EXPECT_FALSE(dialed.has_value());
    EXPECT_GE(std::chrono::steady_clock::now() - started, outcomes.wait());
}

// Outcomes are heard in the order posted, so the one no request waits for
// is dropped before the request's own is heard.
TEST_F(DialOutcomesTest, OutcomeNoRequestWaitsForIsDropped) {
    DialOutcomes outcomes(python_limits());

    post_outcome("nobody", Outcome::Connected, node_reached());

    const auto dialed = outcomes.await(
        [](const string& dial_id) { post_outcome(dial_id, Outcome::ThisNode, node_reached()); });

    ASSERT_TRUE(dialed.has_value());
    EXPECT_NE(dialed->dial_id, "nobody");
    EXPECT_EQ(dialed->outcome, Outcome::ThisNode);
    EXPECT_TRUE(logged("No request waits any longer on dial nobody connected"));
}

TEST_F(DialOutcomesTest, OutcomeComingTooLateIsDropped) {
    DialOutcomes outcomes(brief(), brief_report);
    string late;

    EXPECT_FALSE(outcomes.await([&late](const string& dial_id) { late = dial_id; }).has_value());

    post_outcome(late, Outcome::Connected, node_reached());

    const auto dialed = outcomes.await(
        [](const string& dial_id) { post_outcome(dial_id, Outcome::Unreached); });

    ASSERT_TRUE(dialed.has_value());
    EXPECT_NE(dialed->dial_id, late);
    EXPECT_EQ(dialed->outcome, Outcome::Unreached);
    EXPECT_TRUE(logged("No request waits any longer on dial " + late));
}

TEST_F(DialOutcomesTest, OtherMessageIsLoggedAndDropped) {
    DialOutcomes outcomes(python_limits());

    Channel::post(AddressDialed::name, string("not an outcome"));

    const auto dialed = outcomes.await(
        [](const string& dial_id) { post_outcome(dial_id, Outcome::Connected, node_reached()); });

    ASSERT_TRUE(dialed.has_value());
    EXPECT_EQ(dialed->outcome, Outcome::Connected);
    EXPECT_TRUE(logged("Not what came of a dial: not an outcome"));
}

// As the node stops, so that it need not wait as long as a dial may take.
TEST_F(DialOutcomesTest, StoppingLetsEachRequestWaitingGoAtOnce) {
    DialOutcomes outcomes(python_limits());
    promise<string> first_asked;
    promise<string> second_asked;
    auto first = await_on(outcomes, first_asked);
    auto second = await_on(outcomes, second_asked);

    (void)first_asked.get_future().get();
    (void)second_asked.get_future().get();

    outcomes.stop();

    ASSERT_EQ(first.wait_for(patience), future_status::ready);
    ASSERT_EQ(second.wait_for(patience), future_status::ready);
    EXPECT_FALSE(first.get().has_value());
    EXPECT_FALSE(second.get().has_value());
}

TEST_F(DialOutcomesTest, NoDialIsAskedForOnceStopped) {
    DialOutcomes outcomes(python_limits());
    auto asked = false;

    outcomes.stop();

    EXPECT_FALSE(outcomes.await([&asked](const string& /*dial_id*/) { asked = true; }).has_value());
    EXPECT_FALSE(asked);
}

TEST_F(DialOutcomesTest, GoingLetsEachRequestWaitingGo) {
    auto outcomes = make_unique<DialOutcomes>(python_limits());
    promise<string> asked;
    auto waiting = await_on(*outcomes, asked);

    (void)asked.get_future().get();

    outcomes.reset();

    ASSERT_EQ(waiting.wait_for(patience), future_status::ready);
    EXPECT_FALSE(waiting.get().has_value());
}

// The request no longer waits, so the destructor does not wait for it.
TEST_F(DialOutcomesTest, WhatAskingThrowsIsPassedOn) {
    auto outcomes = make_unique<DialOutcomes>(python_limits());
    string asked;

    EXPECT_THROW((void)outcomes->await(failing_ask(asked)), runtime_error);

    post_outcome(asked, Outcome::Connected, node_reached());

    const auto dialed = outcomes->await(
        [](const string& dial_id) { post_outcome(dial_id, Outcome::Unreached); });

    ASSERT_TRUE(dialed.has_value());
    EXPECT_TRUE(logged("No request waits any longer on dial " + asked));

    outcomes.reset();
}
