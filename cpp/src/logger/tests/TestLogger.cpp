#include <gtest/gtest.h>
#include <cstddef>
#include <cstdint>
#include <exception>
#include <limits>
#include <memory>
#include <mutex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include "logger/ILogFormatter.hpp"
#include "logger/ILogOutput.hpp"
#include "logger/Logger.hpp"

using logger::ILogFormatter;
using logger::ILogOutput;
using logger::Logger;
using std::exception;
using std::mutex;
using std::numeric_limits;
using std::ostringstream;
using std::runtime_error;
using std::scoped_lock;
using std::set;
using std::shared_ptr;
using std::size_t;
using std::string;
using std::string_view;
using std::thread;
using std::uint32_t;
using std::uint64_t;
using std::vector;

namespace {

using Lines = vector<string>;

// The lines an output was given, where a test can read them while the Logger
// owns the output.
class KeptLines {
public:
    void add(string_view line) {
        const scoped_lock lock(m_mutex);

        m_lines.emplace_back(line);
    }

    [[nodiscard]] Lines all() const {
        const scoped_lock lock(m_mutex);

        return m_lines;
    }

private:
    mutable mutex m_mutex;
    Lines m_lines;
};

// Keeps each line it is given.
class KeepingOutput : public ILogOutput {
public:
    explicit KeepingOutput(shared_ptr<KeptLines> lines)
        : m_lines(std::move(lines)) {
    }

    void log(string_view line) override {
        m_lines->add(line);
    }

private:
    shared_ptr<KeptLines> m_lines;
};

// Keeps each line it is asked to write, then fails to write it.
class FailingOutput : public ILogOutput {
public:
    explicit FailingOutput(shared_ptr<KeptLines> attempts)
        : m_attempts(std::move(attempts)) {
    }

    void log(string_view line) override {
        m_attempts->add(line);
        throw runtime_error("Disk full");
    }

private:
    shared_ptr<KeptLines> m_attempts;
};

// What the Logger writes, with a MessageFormatter, when a FailingOutput
// fails.
constexpr auto failure_report =
    "Stopped writing the log to an output that failed: caught Disk full";

// Writes only the message, and an exception as "caught" and its text, so
// that a test can say all that a line holds.
class MessageFormatter : public ILogFormatter {
public:
    [[nodiscard]] string format(string_view message, [[maybe_unused]] size_t thread,
                                [[maybe_unused]] const Logger& logger) const override {
        return string(message);
    }

    [[nodiscard]] string format(const exception& error) const override {
        return string("caught ") + error.what();
    }
};

// Writes only the index of the thread.
class ThreadFormatter : public MessageFormatter {
public:
    [[nodiscard]] string format([[maybe_unused]] string_view message, size_t thread,
                                [[maybe_unused]] const Logger& logger) const override {
        return std::to_string(thread);
    }

    using MessageFormatter::format;
};

// Each test starts with every line shown, written by a MessageFormatter to
// a KeepingOutput, with values padded, and ends with the Logger as it is
// before any is set.
class LoggerTest : public testing::Test {
protected:
    void SetUp() override {
        Logger::remove_all_outputs();
        Logger::add_output(std::make_unique<KeepingOutput>(m_kept));
        Logger::set_formatter(std::make_unique<MessageFormatter>());
        Logger::reset_levels(Logger::Level::Trace);
        Logger::set_inserter_spacing(Logger::InserterSpacing::Pad);
    }

    void TearDown() override {
        Logger::remove_all_outputs();
        Logger::set_formatter(nullptr);
    }

    // The lines written so far.
    [[nodiscard]] Lines lines() const {
        return m_kept->all();
    }

private:
    shared_ptr<KeptLines> m_kept = std::make_shared<KeptLines>();
};

// Whether a Debug line from file is shown, when pattern is all that shows
// Debug lines.
bool shown_by(const string& pattern, string_view file) {
    Logger::reset_levels(Logger::Level::Fatal);
    Logger::set_level(Logger::Level::Debug, pattern);
    return Logger::is_shown(Logger::Level::Debug, file);
}

} // namespace

TEST_F(LoggerTest, WritesTheLineAtTheEndOfTheStatement) {
    lInfo << "Started";

    EXPECT_EQ(lines(), Lines{"Started"});
}

TEST_F(LoggerTest, LineWithNoTextIsNotWritten) {
    lInfo << "";

    EXPECT_TRUE(lines().empty());
}

TEST_F(LoggerTest, ValuesArePadded) {
    constexpr auto port = 8'080;

    lInfo << "Listening on" << port;

    EXPECT_EQ(lines(), Lines{"Listening on 8080"});
}

TEST_F(LoggerTest, ValuesAsIs) {
    constexpr auto port = 8'080;

    Logger::set_inserter_spacing(Logger::InserterSpacing::AsIs);
    lInfo << "port=" << port;

    EXPECT_EQ(lines(), Lines{"port=8080"});
}

TEST_F(LoggerTest, Numbers) {
    constexpr auto negative = -12;
    constexpr auto largest = numeric_limits<uint64_t>::max();
    constexpr auto half = 0.5;
    constexpr auto quarter = 0.25F;

    lInfo << negative << largest << half << quarter;

    EXPECT_EQ(lines(), Lines{"-12 18446744073709551615 0.5 0.25"});
}

TEST_F(LoggerTest, TextOfEachKind) {
    const string text = "string";
    constexpr string_view view = "view";
    const char* const missing = nullptr;

    lInfo << text << view << "literal" << missing;

    EXPECT_EQ(lines(), Lines{"string view literal (null)"});
}

TEST_F(LoggerTest, PointerAsAStreamWritesIt) {
    constexpr auto value = 1;
    ostringstream expected;

    expected << static_cast<const void*>(&value);
    lInfo << &value;

    EXPECT_EQ(lines(), Lines{expected.str()});
}

TEST_F(LoggerTest, ExceptionAsTheFormatterWritesIt) {
    lErr << "Cannot save:" << runtime_error("Disk full");

    EXPECT_EQ(lines(), Lines{"Cannot save: caught Disk full"});
}

TEST_F(LoggerTest, LinesMoreDetailedThanTheLevelAreNotShown) {
    Logger::reset_levels(Logger::Level::Warning);

    lTrace << "trace";
    lVerbose << "verbose";
    lDebug << "debug";
    lInfo << "info";
    lWarn << "warning";
    lErr << "error";
    lLog << "log";

    EXPECT_EQ(lines(), (Lines{"warning", "error", "log"}));
}

TEST_F(LoggerTest, InfoIsMoreSevereThanDebug) {
    Logger::reset_levels(Logger::Level::Info);

    EXPECT_TRUE(Logger::is_shown(Logger::Level::Info));
    EXPECT_FALSE(Logger::is_shown(Logger::Level::Debug));
}

TEST_F(LoggerTest, ResetLevelsForgetsEachPattern) {
    Logger::set_level(Logger::Level::Trace, "src/net/");

    Logger::reset_levels(Logger::Level::Error);

    EXPECT_TRUE(Logger::is_shown(Logger::Level::Error, "src/net/Socket.cpp"));
    EXPECT_FALSE(Logger::is_shown(Logger::Level::Warning, "src/net/Socket.cpp"));
}

TEST_F(LoggerTest, LevelForSomeFiles) {
    Logger::reset_levels(Logger::Level::Warning);

    Logger::set_level(Logger::Level::Debug, "src/net/");

    EXPECT_TRUE(Logger::is_shown(Logger::Level::Debug, "src/net/Socket.cpp"));
    EXPECT_FALSE(Logger::is_shown(Logger::Level::Trace, "src/net/Socket.cpp"));
    EXPECT_FALSE(Logger::is_shown(Logger::Level::Debug, "src/http/Url.hpp"));
    EXPECT_TRUE(Logger::is_shown(Logger::Level::Warning, "src/http/Url.hpp"));
}

TEST_F(LoggerTest, SettingALevelLeavesTheMoreDetailedLevelsUnshown) {
    Logger::reset_levels(Logger::Level::Warning);

    Logger::set_level(Logger::Level::Info, "src/net/");

    EXPECT_FALSE(Logger::is_shown(Logger::Level::Debug, "src/net/Socket.cpp"));
    EXPECT_FALSE(Logger::is_shown(Logger::Level::Trace, "src/http/Url.hpp"));
}

TEST_F(LoggerTest, SettingALevelTakesItsPatternOffMoreDetailedLevels) {
    Logger::reset_levels(Logger::Level::Warning);
    Logger::set_level(Logger::Level::Trace, "src/net/");

    Logger::set_level(Logger::Level::Info, "src/net/");

    EXPECT_TRUE(Logger::is_shown(Logger::Level::Info, "src/net/Socket.cpp"));
    EXPECT_FALSE(Logger::is_shown(Logger::Level::Debug, "src/net/Socket.cpp"));
}

TEST_F(LoggerTest, MacrosLogFromTheFileTheyAreIn) {
    Logger::reset_levels(Logger::Level::Warning);

    Logger::set_level(Logger::Level::Debug, "TestLogger.cpp");
    lDebug << "shown";
    Logger::set_level(Logger::Level::Debug, "-TestLogger.cpp");
    lDebug << "not shown";

    EXPECT_EQ(lines(), Lines{"shown"});
}

TEST_F(LoggerTest, EmptyPatternMatchesEveryFile) {
    EXPECT_TRUE(shown_by("", "src/net/Socket.cpp"));
    EXPECT_TRUE(shown_by("", ""));
}

TEST_F(LoggerTest, DashMatchesNoFile) {
    EXPECT_FALSE(shown_by("-", "src/net/Socket.cpp"));
    EXPECT_FALSE(shown_by("-", ""));
}

TEST_F(LoggerTest, ExclusionsAloneMatchEveryOtherFile) {
    EXPECT_TRUE(shown_by("-tests/", "src/net/Socket.cpp"));
    EXPECT_FALSE(shown_by("-tests/", "src/net/tests/TestSocket.cpp"));
}

TEST_F(LoggerTest, AnInclusionMatchesUnlessAnExclusionDoes) {
    constexpr auto pattern = ".hpp;.cpp;-main.cpp;-tests/";

    EXPECT_TRUE(shown_by(pattern, "src/net/Socket.cpp"));
    EXPECT_TRUE(shown_by(pattern, "src/net/Address.hpp"));
    EXPECT_FALSE(shown_by(pattern, "src/main.cpp"));
    EXPECT_FALSE(shown_by(pattern, "src/net/tests/TestSocket.cpp"));
    EXPECT_FALSE(shown_by(pattern, "README.md"));
}

TEST_F(LoggerTest, OrderOfThePartsDoesNotMatter) {
    EXPECT_FALSE(shown_by("src/include/;-src/", "src/include/Logger.hpp"));
    EXPECT_FALSE(shown_by("-src/include/;src/", "src/include/Logger.hpp"));
    EXPECT_TRUE(shown_by("-src/include/;src/", "src/Logger.cpp"));
}

TEST_F(LoggerTest, ConditionalLinesOnlyWhenTheConditionIsTrue) {
    const auto full = true;
    const auto empty = false;

    lErrIf(full) << "full";
    lErrIf(empty) << "empty";
    lWarnIf(full) << "warned";
    lWarnIf(empty) << "not warned";
    lFatalIf(empty) << "still running";

    EXPECT_EQ(lines(), (Lines{"full", "warned"}));
}

TEST_F(LoggerTest, SaysWhereItWasMade) {
    constexpr uint32_t line = 88;

    const Logger made(Logger::Level::Warning, "src/net/Socket.cpp", line, "send", true, "full");

    EXPECT_EQ(made.level(), Logger::Level::Warning);
    EXPECT_EQ(made.file(), "src/net/Socket.cpp");
    EXPECT_EQ(made.line(), line);
    EXPECT_EQ(made.function(), "send");
    EXPECT_EQ(made.condition(), "full");
}

TEST_F(LoggerTest, MacrosSayWhereTheLineWasLogged) {
    const auto full = true;

    Logger::set_formatter(nullptr);
    const auto line = __LINE__ + 1;
    lWarnIf(full) << "Disk";

    const auto written = lines();
    ASSERT_EQ(written.size(), 1U);
    EXPECT_NE(written.at(0).find("[WRN]"), string::npos) << written.at(0);
    const auto where = "TestLogger.cpp:" + std::to_string(line) + "][TestBody][full] Disk\n";
    EXPECT_NE(written.at(0).find(where), string::npos) << written.at(0);
}

TEST_F(LoggerTest, NullOutputIsLeftOut) {
    Logger::add_output(nullptr);

    lInfo << "Started";

    EXPECT_EQ(lines(), Lines{"Started"});
}

TEST_F(LoggerTest, OutputThatFailsIsReportedAndRemoved) {
    const auto attempts = std::make_shared<KeptLines>();

    Logger::add_output(std::make_unique<FailingOutput>(attempts));
    lInfo << "first";
    lInfo << "second";

    EXPECT_EQ(attempts->all(), Lines{"first"});
    EXPECT_EQ(lines(), (Lines{"first", failure_report, "second"}));
}

TEST_F(LoggerTest, WithNoOutputLinesGoToStderr) {
    Logger::remove_all_outputs();

    testing::internal::CaptureStderr();
    lInfo << "to stderr";

    EXPECT_EQ(testing::internal::GetCapturedStderr(), "to stderr");
}

TEST_F(LoggerTest, AfterEveryOutputFailsLinesGoToStderr) {
    const auto attempts = std::make_shared<KeptLines>();

    Logger::remove_all_outputs();
    Logger::add_output(std::make_unique<FailingOutput>(attempts));
    testing::internal::CaptureStderr();
    lInfo << "lost";
    lInfo << "to stderr";

    EXPECT_EQ(testing::internal::GetCapturedStderr(), string(failure_report) + "to stderr");
}

TEST_F(LoggerTest, EachThreadHasAnIndexOfItsOwn) {
    Logger::set_formatter(std::make_unique<ThreadFormatter>());

    lInfo << "first";
    lInfo << "second";
    thread other([] { lInfo << "other"; });
    other.join();

    const auto written = lines();
    ASSERT_EQ(written.size(), 3U);
    EXPECT_EQ(written.at(0), written.at(1));
    EXPECT_NE(written.at(0), written.at(2));
}

TEST_F(LoggerTest, EveryLineFromManyThreadsIsWrittenWhole) {
    constexpr auto thread_count = 8;
    constexpr auto lines_each = 100;
    vector<thread> threads;
    set<string> expected;

    for (auto number = 0; number < thread_count; ++number) {
        for (auto line = 0; line < lines_each; ++line) {
            expected.insert("thread " + std::to_string(number) + " line " + std::to_string(line));
        }

        threads.emplace_back([number] {
            for (auto line = 0; line < lines_each; ++line) {
                lInfo << "thread" << number << "line" << line;
            }
        });
    }

    for (auto& writer : threads) {
        writer.join();
    }

    const auto written = lines();
    EXPECT_EQ(written.size(), expected.size());
    EXPECT_EQ(set<string>(written.begin(), written.end()), expected);
}

TEST(LoggerDeathTest, FatalLineEndsTheProgram) {
    GTEST_FLAG_SET(death_test_style, "threadsafe");
    Logger::remove_all_outputs();
    Logger::set_formatter(nullptr);
    Logger::reset_levels(Logger::Level::Fatal);

    EXPECT_DEATH(lFatal << "Out of memory", R"(\[FTL\].* Out of memory)");
}

TEST(LoggerDeathTest, FatalLineWithNoTextEndsTheProgram) {
    GTEST_FLAG_SET(death_test_style, "threadsafe");
    Logger::remove_all_outputs();
    Logger::set_formatter(nullptr);
    Logger::reset_levels(Logger::Level::Fatal);

    EXPECT_DEATH(lFatal << "", R"(\[FTL\])");
}

TEST(LoggerDeathTest, FatalIfEndsTheProgramWhenTrue) {
    GTEST_FLAG_SET(death_test_style, "threadsafe");
    const auto broken = true;
    Logger::remove_all_outputs();
    Logger::set_formatter(nullptr);
    Logger::reset_levels(Logger::Level::Fatal);

    EXPECT_DEATH(lFatalIf(broken) << "Broken", R"(\[broken\] Broken)");
}
