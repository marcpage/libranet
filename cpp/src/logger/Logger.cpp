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

namespace {

// What every Logger shares: where lines go, how they are written, which are
// shown, and the threads that have logged, in the order they first did. The
// mutex guards all but the spacing, and is held while a line is written, so
// that lines from different threads do not mix.
struct Shared {
    std::mutex mutex;
    std::vector<logger::ILogOutput::Ptr> outputs;
    logger::ILogFormatter::Ptr formatter = std::make_unique<logger::DefaultFormatter>();
    std::map<logger::Logger::Level, std::string> levels;
    std::vector<std::thread::id> threads;
    std::atomic<logger::Logger::InserterSpacing> spacing{logger::Logger::InserterSpacing::Pad};
};

// Made when first used, so that it is ready for a line logged while another
// static object is built.
Shared& shared() {
    static Shared state;
    return state;
}

// Whether file matches pattern, as Logger::set_level() says.
bool file_matches(std::string_view file, std::string_view pattern) {
    auto has_inclusions = false;
    auto included = false;
    std::size_t start = 0;

    while (start <= pattern.size()) {
        const auto end = std::min(pattern.find(';', start), pattern.size());
        const auto part = pattern.substr(start, end - start);
        const auto excludes = !part.empty() && '-' == part.front();
        const auto contained = std::string_view::npos != file.find(part.substr(excludes ? 1 : 0));

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
// shared mutex.
bool shown(const std::map<logger::Logger::Level, std::string>& levels,
           logger::Logger::Level level, std::string_view file) {
    return std::any_of(levels.lower_bound(level), levels.end(),
                       [file](const auto& entry) { return file_matches(file, entry.second); });
}

// The index of the calling thread among those that have logged, in the order
// they first did. The caller holds the shared mutex.
std::size_t thread_index(std::vector<std::thread::id>& threads) {
    const auto this_thread = std::this_thread::get_id();
    const auto found = std::find(threads.begin(), threads.end(), this_thread);

    if (found != threads.end()) {
        return static_cast<std::size_t>(std::distance(threads.begin(), found));
    }

    threads.push_back(this_thread);
    return threads.size() - 1;
}

// Writes the line to each output, or to stderr if there are none. An output
// that fails is removed, and what the formatter makes of what it threw is
// returned. The caller holds the shared mutex.
std::vector<std::string> write_to_outputs(Shared& state, std::string_view line) {
    std::vector<std::string> failures;

    if (state.outputs.empty()) {
        state.outputs.push_back(std::make_unique<logger::StdErrOutput>());
    }

    for (auto output = state.outputs.begin(); output != state.outputs.end();) {
        try {
            (*output)->log(line);
            ++output;
        } catch (const std::exception& error) {
            failures.push_back(state.formatter->format(error));
            output = state.outputs.erase(output);
        }
    }

    return failures;
}

} // namespace

namespace logger {

void Logger::add_output(ILogOutput::Ptr output) {
    if (!output) {
        return;
    }

    auto& state = shared();
    const std::scoped_lock lock(state.mutex);

    state.outputs.push_back(std::move(output));
}

void Logger::remove_all_outputs() {
    auto& state = shared();
    const std::scoped_lock lock(state.mutex);

    state.outputs.clear();
}

void Logger::set_formatter(ILogFormatter::Ptr new_formatter) {
    if (!new_formatter) {
        new_formatter = std::make_unique<DefaultFormatter>();
    }

    auto& state = shared();
    const std::scoped_lock lock(state.mutex);

    state.formatter = std::move(new_formatter);
}

void Logger::set_level(Level level, std::string pattern) {
    auto& state = shared();
    const std::scoped_lock lock(state.mutex);

    // Left at a more detailed level, the pattern would still show its files'
    // lines at that level.
    for (auto entry = state.levels.lower_bound(level); entry != state.levels.end();) {
        entry = pattern == entry->second ? state.levels.erase(entry) : std::next(entry);
    }

    state.levels[level] = std::move(pattern);
}

void Logger::reset_levels(Level level) {
    auto& state = shared();
    const std::scoped_lock lock(state.mutex);

    state.levels.clear();
    state.levels[level] = "";
}

bool Logger::is_shown(Level level, std::string_view file) {
    auto& state = shared();
    const std::scoped_lock lock(state.mutex);

    return shown(state.levels, level, file);
}

void Logger::set_inserter_spacing(InserterSpacing new_spacing) {
    shared().spacing = new_spacing;
}

Logger::Logger(Level level, std::string_view file, std::uint32_t line, std::string_view function,
               bool condition_met, std::string_view condition)
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
            _write();
        }
    } catch (const std::exception& error) {
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

std::string_view Logger::file() const noexcept {
    return m_file;
}

std::uint32_t Logger::line() const noexcept {
    return m_line;
}

std::string_view Logger::function() const noexcept {
    return m_function;
}

std::string_view Logger::condition() const noexcept {
    return m_condition;
}

Logger& Logger::operator<<(std::string_view value) {
    return _append(value);
}

Logger& Logger::operator<<(const char* value) {
    if (nullptr == value) {
        return _append("(null)");
    }

    return _append(value);
}

Logger& Logger::operator<<(const void* value) {
    std::ostringstream text;

    text << value;
    return _append(text.str());
}

Logger& Logger::operator<<(double value) {
    std::ostringstream text;

    text << value;
    return _append(text.str());
}

Logger& Logger::operator<<(const std::exception& value) {
    auto& state = shared();
    const std::scoped_lock lock(state.mutex);

    return _append(state.formatter->format(value));
}

Logger& Logger::_append(std::string_view value) {
    if (InserterSpacing::Pad == shared().spacing && !m_text.empty()) {
        m_text += ' ';
    }

    m_text += value;
    return *this;
}

void Logger::_write() const {
    auto& state = shared();
    const std::scoped_lock lock(state.mutex);

    if (!shown(state.levels, m_level, m_file)) {
        return;
    }

    const auto thread = thread_index(state.threads);
    const auto failures = write_to_outputs(state, state.formatter->format(m_text, thread, *this));

    // Those left are told of each output that failed. One that fails then is
    // removed too.
    for (const auto& failure : failures) {
        const auto report = "Stopped writing the log to an output that failed: " + failure;

        (void)write_to_outputs(state, state.formatter->format(report, thread, *this));
    }
}

} // namespace logger
