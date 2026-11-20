#include <gtest/gtest.h>
#include <cstddef>
#include <cstdint>
#include <regex>
#include <stdexcept>
#include <string>

#include "logger/DefaultFormatter.hpp"
#include "logger/Logger.hpp"

using logger::DefaultFormatter;
using logger::Logger;

namespace {

// The pattern of the time a line starts with: in local time, with its offset
// from GMT, or in GMT.
std::string time_pattern(DefaultFormatter::Location location) {
    std::string pattern = R"(\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})";

    if (DefaultFormatter::Location::Local == location) {
        pattern += R"( [+-]\d{4})";
    }

    return pattern + R"( \([A-Z][a-z]{2}\)\])";
}

// Whether text is all that pattern matches, as ECMAScript reads it.
bool matches(const std::string& text, const std::string& pattern) {
    return std::regex_match(text, std::regex(pattern));
}

} // namespace

TEST(DefaultFormatterTest, LevelStrings) {
    EXPECT_EQ(DefaultFormatter::level_string(Logger::Level::Fatal), "FTL");
    EXPECT_EQ(DefaultFormatter::level_string(Logger::Level::Log), "LOG");
    EXPECT_EQ(DefaultFormatter::level_string(Logger::Level::Error), "ERR");
    EXPECT_EQ(DefaultFormatter::level_string(Logger::Level::Warning), "WRN");
    EXPECT_EQ(DefaultFormatter::level_string(Logger::Level::Info), "NFO");
    EXPECT_EQ(DefaultFormatter::level_string(Logger::Level::Debug), "DBG");
    EXPECT_EQ(DefaultFormatter::level_string(Logger::Level::Verbose), "VBS");
    EXPECT_EQ(DefaultFormatter::level_string(Logger::Level::Trace), "TRC");
}

TEST(DefaultFormatterTest, LineInLocalTime) {
    constexpr std::uint32_t line_number = 88;
    constexpr std::size_t thread = 3;
    const DefaultFormatter formatter;
    const Logger source(Logger::Level::Warning, "src/net/Socket.cpp", line_number, "send");

    const auto line = formatter.format("Peer gone", thread, source);

    const std::string rest = R"(\[3\]\[WRN\]\[src/net/Socket\.cpp:88\]\[send\] Peer gone\n)";

    EXPECT_TRUE(matches(line, time_pattern(DefaultFormatter::Location::Local) + rest)) << line;
}

TEST(DefaultFormatterTest, LineInGmt) {
    const DefaultFormatter formatter(DefaultFormatter::Location::Gmt);
    const Logger source(Logger::Level::Info);

    const auto line = formatter.format("Started", 0, source);

    const std::string rest = R"(\[0\]\[NFO\] Started\n)";

    EXPECT_TRUE(matches(line, time_pattern(DefaultFormatter::Location::Gmt) + rest)) << line;
}

TEST(DefaultFormatterTest, LineWithItsCondition) {
    const DefaultFormatter formatter;
    const Logger source(Logger::Level::Error, "Store.cpp", 1, "put", true, "disk_full");

    const auto line = formatter.format("No room", 1, source);

    const std::string rest = R"(\[1\]\[ERR\]\[Store\.cpp:1\]\[put\]\[disk_full\] No room\n)";

    EXPECT_TRUE(matches(line, time_pattern(DefaultFormatter::Location::Local) + rest)) << line;
}

TEST(DefaultFormatterTest, Exception) {
    const DefaultFormatter formatter;

    EXPECT_EQ(formatter.format(std::runtime_error("Disk full")), "Exception: Disk full");
}
