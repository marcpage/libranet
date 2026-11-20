#pragma once

#define lFatal core::Logger(core::Logger::Level::Fatal, __FILE__, __LINE__, __func__)
#define lFatalIf(condition) core::Logger(core::Logger::Level::Fatal, __FILE__, __LINE__, __func__, condition, #condition)
#define lLog core::Logger(core::Logger::Level::Log, __FILE__, __LINE__, __func__)
#define lErr core::Logger(core::Logger::Level::Error, __FILE__, __LINE__, __func__)
#define lErrIf(condition) core::Logger(core::Logger::Level::Error, __FILE__, __LINE__, __func__, condition, #condition)
#define lWarn core::Logger(core::Logger::Level::Warning, __FILE__, __LINE__, __func__)
#define lWarnIf(condition) core::Logger(core::Logger::Level::Warning, __FILE__, __LINE__, __func__, condition, #condition)
#define lInfo core::Logger(core::Logger::Level::Info, __FILE__, __LINE__, __func__)
#define lDebug core::Logger(core::Logger::Level::Debug, __FILE__, __LINE__, __func__)
#define lVerbose core::Logger(core::Logger::Level::Verbose, __FILE__, __LINE__, __func__)
#define lTrace core::Logger(core::Logger::Level::Trace, __FILE__, __LINE__, __func__)

#include "ILogFormatter.h"
#include "ILogOutput.h"
#include <chrono>
#include <exception>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <type_traits>
#include <vector>

namespace core {

class Logger {
public:
    enum Level {
        Fatal = 0, // qFatal() << "message";
        Log = 1,
        Error = 2, // qCrticial() << "message";
        Warning = 3, // qWarning() << "mesage";
        Debug = 4, // qDebug() << "message";
        Info = 5, // qInfo() << "message";
        Verbose = 6,
        Trace = 7,
    };

    typedef std::unique_ptr<ILogOutput> ILogOutputPtr;
    typedef std::unique_ptr<ILogFormatter> ILogFormatterPtr;
    enum InserterSpacing {InserterPad, InserterAsIs};

    static void addOutput(ILogOutputPtr method);
    static void removeAllOutputs();
    static void setFormat(ILogFormatterPtr formatter);
    static void setLevel(Level level, const std::string& pattern="");
    static void resetLevels(Level level);
    static bool shown(Level loggedLevel, const std::string& file="");
    static void setInserterSpacing(InserterSpacing spacing);

    explicit Logger(Level level, const char* filename=nullptr, const int lineNumber=0, const char* functionName=nullptr, bool doLog=true, const char* conditionText=nullptr);
    Logger(const Logger&) = delete;
    Logger& operator=(const Logger&) = delete;
    ~Logger();

    Logger& log_line(const std::string& line);
    template<typename T>
    T logExpression(const std::string& flow, const std::string& expression, T result);
    bool logExpressionBool(const std::string& flow, const std::string& expression, bool result);

    template<typename T, typename=typename std::enable_if<std::is_arithmetic<T>::value>::type>
    Logger& operator<<(const T& value);
    
    Logger& operator<<(const std::string& value);
    Logger& operator<<(const char* value);
    Logger& operator<<(const void* value);
    Logger& operator<<(float value);
    Logger& operator<<(double value);
    Logger& operator<<(const std::exception& value);

    const Level levelRequested;
    const char* file;
    const int line;
    const char* function;
    const char* condition;

private:
    std::string _stream;
    const bool _doLog;
    enum Mutex {ThreadListMutex, OutputListMutex, FormatterMutex, LevelsMutex};

    static std::mutex& _mutex(Mutex mutex);
    static size_t _threadIndex();

    static std::vector<ILogOutputPtr> _outputs_needsLock;
    static ILogFormatterPtr _formatter_needsLock;
    static std::map<Level, std::string> _levels_needsLock;
    static std::vector<std::thread::id> _threads_needsLock;
    static InserterSpacing _spacing;

    static bool _fileMatches(const std::string& file, const std::string& pattern);

    Logger& _append(const std::string& value);
    Logger& _logLineCore(const std::string& line);
};

template<typename T>
inline T Logger::logExpression(const std::string& flow, const std::string& expression, T result) {
    log_line(flow + ": " + expression + " => " + std::to_string(result));
    return result;
}

template<typename T, typename>
inline Logger& Logger::operator<<(const T& value) {
    return _append(std::to_string(static_cast<typename std::conditional<
        std::is_floating_point<T>::value, double,
        typename std::conditional<std::is_signed<T>::value, long long, unsigned long long>::type
    >::type>(value)));
}

}

// clang-format off

#ifdef ENABLE_IF_TRACE
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wkeyword-macro"
#define if(expression) if(core::Logger(core::Logger::Level::Trace, __FILE__, __LINE__, __func__).logExpressionBool("if", #expression, expression))
#pragma GCC diagnostic pop
#endif

#ifdef ENABLE_WHILE_TRACE
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wkeyword-macro"
#define while(expression) while(core::Logger(core::Logger::Level::Trace, __FILE__, __LINE__, __func__).logExpressionBool("while", #expression, expression))
#pragma GCC diagnostic pop
#endif

#ifdef ENABLE_SWITCH_TRACE
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wkeyword-macro"
#define switch(expression) switch(core::Logger(core::Logger::Level::Trace, __FILE__, __LINE__, __func__).logExpression("switch", #expression, expression))
#pragma GCC diagnostic pop
#endif

// clang-format on