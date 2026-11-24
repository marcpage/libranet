#include "Logger.hpp"

#include <algorithm>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <iterator>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include "DefaultFormatter.hpp"
#include "ILogFormatter.hpp"
#include "ILogOutput.hpp"
#include "StdErrOutput.hpp"

using logger::DefaultFormatter;
using logger::ILogFormatter;
using logger::ILogOutput;
using logger::Logger;
using logger::StdErrOutput;
using std::atomic;
using std::exception;
using std::map;
using std::mutex;
using std::ostringstream;
using std::scoped_lock;
using std::size_t;
using std::string;
using std::string_view;
using std::thread;
using std::uint32_t;
using std::vector;

namespace {

// What every Logger shares: where lines go, how they are written, which are
// shown, and the threads that have logged, in the order they first did. The
// guard is held to read or change any of them but the spacing, and while a
// line is written, so that lines from different threads do not mix.
struct Shared {
    mutex guard;
    vector<ILogOutput::Ptr> outputs;
    ILogFormatter::Ptr formatter = std::make_unique<DefaultFormatter>();
    map<Logger::Level, string> levels;
    vector<thread::id> threads;
    atomic<Logger::InserterSpacing> spacing{Logger::InserterSpacing::Pad};
};

// Made when first used, so that it is ready for a line logged while another
// static object is built.
Shared& shared() {
    static Shared state;
    return state;
}

// Whether file matches pattern, as Logger::set_level() says.
bool file_matches(string_view file, string_view pattern) {
    auto has_inclusions = false;
    auto included = false;
    size_t start = 0;

    while (start <= pattern.size()) {
        const auto end = std::min(pattern.find(';', start), pattern.size());
        const auto part = pattern.substr(start, end - start);
        const auto excludes = !part.empty() && '-' == part.front();
        const auto contained = string_view::npos != file.find(part.substr(excludes ? 1 : 0));

        if (excludes && contained) {
            return false;
        }

        if (!excludes) {
            has_inclusions = true;
            included = included || contained;
        }

        start = end + 1;
    }

    return included || !has_inclusions;
}

// Whether a line at level, from file, is shown. A level shows its lines and
// those of every level more severe, which sort before it, so the levels that
// can show the line are this one and those after it. The caller holds the
// shared guard.
bool shown(const map<Logger::Level, string>& levels,
           Logger::Level level, string_view file) {
    return std::any_of(levels.lower_bound(level), levels.end(),
                       [file](const auto& entry) { return file_matches(file, entry.second); });
}

// The index of the calling thread among those that have logged, in the order
// they first did. The caller holds the shared guard.
size_t thread_index(vector<thread::id>& threads) {
    const auto this_thread = std::this_thread::get_id();
    const auto found = std::find(threads.begin(), threads.end(), this_thread);

    if (found != threads.end()) {
        return static_cast<size_t>(std::distance(threads.begin(), found));
    }

    threads.push_back(this_thread);
    return threads.size() - 1;
}

// Writes the line to each output, or to stderr if there are none. An output
// that fails is removed, and what the formatter makes of what it threw is
// returned. The caller holds the shared guard.
vector<string> write_to_outputs(Shared& state, string_view line) {
    vector<string> failures;

    if (state.outputs.empty()) {
        state.outputs.push_back(std::make_unique<StdErrOutput>());
    }

    for (auto output = state.outputs.begin(); output != state.outputs.end();) {
        try {
            (*output)->log(line);
            ++output;
        } catch (const exception& error) {
            failures.push_back(state.formatter->format(error));
            output = state.outputs.erase(output);
        }
    }

    return failures;
}

// Writes text, what line holds, to each output, if line's level shows lines
// from its file.
void write_line(const Logger& line, const string& text) {
    auto& state = shared();
    const scoped_lock lock(state.guard);

    if (!shown(state.levels, line.level(), line.file())) {
        return;
    }

    const auto index = thread_index(state.threads);
    const auto failures = write_to_outputs(state, state.formatter->format(text, index, line));

    // Those left are told of each output that failed. One that fails then is
    // removed too.
    for (const auto& failure : failures) {
        const auto report = "Stopped writing the log to an output that failed: " + failure;

        (void)write_to_outputs(state, state.formatter->format(report, index, line));
    }
}

} // namespace

namespace logger {

void Logger::add_output(ILogOutput::Ptr output) {
    if (!output) {
        return;
    }

    auto& state = shared();
    const scoped_lock lock(state.guard);

    state.outputs.push_back(std::move(output));
}

void Logger::remove_all_outputs() {
    auto& state = shared();
    const scoped_lock lock(state.guard);

    state.outputs.clear();
}

void Logger::set_formatter(ILogFormatter::Ptr new_formatter) {
    if (!new_formatter) {
        new_formatter = std::make_unique<DefaultFormatter>();
    }

    auto& state = shared();
    const scoped_lock lock(state.guard);

    state.formatter = std::move(new_formatter);
}

void Logger::set_level(Level level, string pattern) {
    auto& state = shared();
    const scoped_lock lock(state.guard);

    // Left at a more detailed level, the pattern would still show its files'
    // lines at that level.
    for (auto entry = state.levels.lower_bound(level); entry != state.levels.end();) {
        entry = pattern == entry->second ? state.levels.erase(entry) : std::next(entry);
    }

    state.levels[level] = std::move(pattern);
}

void Logger::reset_levels(Level level) {
    auto& state = shared();
    const scoped_lock lock(state.guard);

    state.levels.clear();
    state.levels[level] = "";
}

bool Logger::is_shown(Level level, string_view file) {
    auto& state = shared();
    const scoped_lock lock(state.guard);

    return shown(state.levels, level, file);
}

void Logger::set_inserter_spacing(InserterSpacing new_spacing) {
    shared().spacing = new_spacing;
}

Logger::Logger(Level level, string_view file, uint32_t line, string_view function,
               bool condition_met, string_view condition)
    : m_level(level)
    , m_file(file)
    , m_line(line)
    , m_function(function)
    , m_condition_met(condition_met)
    , m_condition(condition) {
}

Logger::~Logger() {
    if (!m_condition_met) {
        return;
    }

    // The log is no reason to stop the program, so a line that cannot be
    // written is only reported, where it can be.
    try {
        if (Level::Fatal == m_level || !m_text.empty()) {
            write_line(*this, m_text);
        }
    } catch (const exception& error) {
        (void)std::fputs("Cannot write a line of the log: ", stderr);
        (void)std::fputs(error.what(), stderr);
        (void)std::fputs("\n", stderr);
    }

    if (Level::Fatal == m_level) {
        std::abort();
    }
}

Logger::Level Logger::level() const noexcept {
    return m_level;
}

string_view Logger::file() const noexcept {
    return m_file;
}

uint32_t Logger::line() const noexcept {
    return m_line;
}

string_view Logger::function() const noexcept {
    return m_function;
}

string_view Logger::condition() const noexcept {
    return m_condition;
}

Logger& Logger::operator<<(string_view value) {
    return _append(value);
}

Logger& Logger::operator<<(const char* value) {
    if (nullptr == value) {
        return _append("(null)");
    }

    return _append(value);
}

Logger& Logger::operator<<(const void* value) {
    ostringstream text;

    text << value;
    return _append(text.str());
}

Logger& Logger::operator<<(double value) {
    ostringstream text;

    text << value;
    return _append(text.str());
}

Logger& Logger::operator<<(const exception& value) {
    auto& state = shared();
    const scoped_lock lock(state.guard);

    return _append(state.formatter->format(value));
}

Logger& Logger::_append(string_view value) {
    if (InserterSpacing::Pad == shared().spacing && !m_text.empty()) {
        m_text += ' ';
    }

    m_text += value;
    return *this;
}

} // namespace logger
