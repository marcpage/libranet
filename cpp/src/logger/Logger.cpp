#include "Logger.h"
#include "StdErrOutput.h"
#include <algorithm>
#include <sstream>
#include "DefaultFormatter.h"

namespace core {

// #MARK: outputs list mutex

// NOLINTNEXTLINE(cppcoreguidelines-avoid-non-const-global-variables)
std::vector<Logger::ILogOutputPtr> Logger::_outputs_needsLock;

void Logger::addOutput(ILogOutputPtr method) {
    if (method != nullptr) {
        std::lock_guard<std::mutex> lock(_mutex(OutputListMutex));

        _outputs_needsLock.push_back(std::move(method));
    }
}

void Logger::removeAllOutputs() {
    std::lock_guard<std::mutex> lock(_mutex(OutputListMutex));

    _outputs_needsLock.clear();
}

// MARK: formatter mutex

// NOLINTNEXTLINE(cppcoreguidelines-avoid-non-const-global-variables)
Logger::ILogFormatterPtr Logger::_formatter_needsLock;

void Logger::setFormat(ILogFormatterPtr formatter) {
    std::lock_guard<std::mutex> lock(_mutex(FormatterMutex));

    _formatter_needsLock = std::move(formatter);
}

Logger &Logger::operator<<(const std::exception &value) {
    std::lock_guard<std::mutex> lock(_mutex(FormatterMutex));

    return _append(_formatter_needsLock->format(value));
}

// MARK: outputs list and formatter mutexes

Logger &Logger::_logLineCore(const std::string &logLine) {
    std::lock_guard<std::mutex> lockOutput(_mutex(OutputListMutex));
    std::lock_guard<std::mutex> lockFormatter(_mutex(FormatterMutex));
    using ExceptionLogger = std::pair<std::string, std::string>;
    using ExceptionList = std::vector<ExceptionLogger>;
    ExceptionList failed_outputs;

    if (nullptr == _formatter_needsLock) {
        _formatter_needsLock = std::make_unique<DefaultFormatter>();
    }

    const auto formatted_line = _formatter_needsLock->format(logLine, _threadIndex(), *this);

    if (_outputs_needsLock.empty()) {
        _outputs_needsLock.push_back(std::make_unique<StdErrOutput>());
    }

    for (auto output = _outputs_needsLock.begin(); output != _outputs_needsLock.end();) {
        try {
            (*output)->log(formatted_line);
            ++output;
        } catch (const std::exception &exception) {
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wpotentially-evaluated-expression"
            const auto * const loggerType = *output ? typeid(**output).name() : "";
#pragma GCC diagnostic pop

            failed_outputs.push_back(ExceptionLogger(_formatter_needsLock->format(exception), loggerType));
            output = _outputs_needsLock.erase(output);
        }
    }

    if (_outputs_needsLock.empty()) {
        _outputs_needsLock.push_back(std::make_unique<StdErrOutput>());
    }

    if (!failed_outputs.empty()) {
        for (const auto &exceptionLogger : failed_outputs) {
            for (auto &sink : _outputs_needsLock) {
                try {
                    sink->log(_formatter_needsLock->format("Logger[" + exceptionLogger.second + "]: "
                            + exceptionLogger.first,
                        _threadIndex(), *this));
                } catch (const std::exception &) {
                    // we tried
                }
            }
        }
    }

    return *this;
}

// MARK: levels mutex

// NOLINTNEXTLINE(cppcoreguidelines-avoid-non-const-global-variables)
std::map<Logger::Level, std::string> Logger::_levels_needsLock;

void Logger::setLevel(Level level, const std::string &pattern) {
    std::lock_guard<std::mutex> lock(_mutex(LevelsMutex));
    const int start = static_cast<int>(level);
    const int max = static_cast<int>(Level::Trace);

    for (int index = start; index <= max; ++index) {
        const auto lvl = static_cast<Level>(index);
        if (pattern == _levels_needsLock[lvl] && !pattern.empty() && pattern != "-") {
            _levels_needsLock.erase(lvl);
        }
    }

    _levels_needsLock[level] = pattern;
}

void Logger::resetLevels(Level level) {
    std::lock_guard<std::mutex> lock(_mutex(LevelsMutex));

    _levels_needsLock.clear();
    _levels_needsLock[level] = "";
}

bool Logger::shown(Level loggedLevel, const std::string &file) {
    // If the file has been set to the requested level or higher, it can be logged.
    std::lock_guard<std::mutex> lock(_mutex(LevelsMutex));
    const int start = static_cast<int>(loggedLevel);
    const int max = static_cast<int>(Level::Trace);

    for (int index = start; index <= max; ++index) {
        const auto lvl = static_cast<Level>(index);
        const auto found = _levels_needsLock.find(lvl);

        if (found == _levels_needsLock.end()) {
            continue; // nothing authorized for this level
        }

        if (_fileMatches(file, found->second)) {
            return true;
        }
    }

    return false;
}

// MARK: spacing

// NOLINTNEXTLINE(cppcoreguidelines-avoid-non-const-global-variables)
Logger::InserterSpacing Logger::_spacing;

void Logger::setInserterSpacing(InserterSpacing spacing) {
    _spacing = spacing;
}

Logger &Logger::_append(const std::string &value) {
    if (InserterPad == _spacing && !_stream.empty()) {
        _stream += ' ';
    }

    _stream += value;
    return *this;
}

// MARK: Con/De strutors

Logger::Logger(Level level, const char *filename, const int lineNumber, const char *functionName, bool doLog, const char *conditionText)
    : levelRequested(level)
    , file(filename)
    , line(lineNumber)
    , function(functionName)
    , condition(conditionText)
    , _doLog(doLog) { }

Logger::~Logger() {
    if (levelRequested != Level::Fatal && (!_doLog || _stream.empty())) {
        return;
    }

    try {
        log_line(_stream);
    } catch (...) {
        // too late
    }

    if (_doLog && levelRequested == Level::Fatal) {
        ::abort();
    }
}

// MARK: logging methods

Logger &Logger::log_line(const std::string &logLine) {
    if (!shown(levelRequested, nullptr == file ? "" : file)) {
        return *this;
    }

    return _logLineCore(logLine);
}

bool Logger::logExpressionBool(const std::string &flow, const std::string &expression, bool result) {
    log_line(flow + ": " + expression + " => " + (result ? "true" : "false"));
    return result;
}

Logger &Logger::operator<<(const std::string &value) {
    return _append(value);
}

Logger &Logger::operator<<(const char *value) {
    return _append(value);
}

Logger &Logger::operator<<(const void *value) {
    std::ostringstream oss;

    oss << std::showbase << std::uppercase << std::hex << value;
    return _append(oss.str());
}

Logger &Logger::operator<<(float value) {
    return (*this) << static_cast<double>(value);
}

Logger &Logger::operator<<(double value) {
    std::ostringstream oss;

    oss << value;
    return _append(oss.str());
}

// MARK: internals

std::mutex &Logger::_mutex(Mutex mutexType) {
    static std::mutex threadListMutex;
    static std::mutex outputListMutex;
    static std::mutex formatterMutex;
    static std::mutex levelsMutex;

    switch (mutexType) {
    case ThreadListMutex:
        return threadListMutex;
    case OutputListMutex:
        return outputListMutex;
    case FormatterMutex:
        return formatterMutex;
    case LevelsMutex:
        return levelsMutex;
    default:
        throw std::invalid_argument("Invalid mutex type: " + std::to_string(static_cast<int>(mutexType)));
    }
}

bool Logger::_fileMatches(const std::string &file, const std::string &pattern) {
    /*
        Rules:
        1. No files matches a pattern of "-"
        2. Any file matches an empty pattern (including an empty file)
        3. All positive patterns are ORed and all negative patterns are ANDed

        Example:
        - "-" match nothing
        - "" match everything
        - "-bin/" match everything except files that contain "bin/"
        - "src/;-src/include/" match everything that contains "src/"
                                unless it contains "src/include/"
        - ".h;.cpp;-main.cpp;-test.cpp" match all files that have ".h" or ".cpp" in them
                                            but don't match any that contain "main.cpp"
                                                or "test.cpp"
    */
    size_t start = 0;
    auto good = false;

    if (pattern.empty()) {
        return true;
    }

    if (pattern == "-") {
        return false;
    }

    while (start < pattern.size()) {
        const auto end = pattern.find(';', start);
        const auto part = pattern.substr(start, end - start);
        const auto negative = part.find('-') == 0;
        const auto name = part.substr(negative ? 1 : 0);
        const auto matches = file.find(name) != std::string::npos;

        if (matches) {
            good = !negative;
        }

        start = end < pattern.size() ? end + 1 : end;
    }

    return good;
}

// MARK: threads mutex

// NOLINTNEXTLINE(cppcoreguidelines-avoid-non-const-global-variables)
std::vector<std::thread::id> Logger::_threads_needsLock;

size_t Logger::_threadIndex() {
    const auto this_thread = std::this_thread::get_id();
    std::lock_guard<std::mutex> protection(_mutex(ThreadListMutex));
    const auto found = std::find(_threads_needsLock.begin(), _threads_needsLock.end(), this_thread);

    if (found == _threads_needsLock.end()) {
        _threads_needsLock.push_back(this_thread);
        return _threads_needsLock.size() - 1;
    }

    return static_cast<size_t>(found - _threads_needsLock.begin());
}

}
