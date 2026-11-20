# The log: lines from any thread, at levels, to stderr, files, or syslog.
add_library(libranet_logger STATIC
    DefaultFormatter.cpp
    FileOutput.cpp
    Logger.cpp
    StreamOutput.cpp
    SysLogOutput.cpp)
add_library(libranet::logger ALIAS libranet_logger)
target_include_directories(libranet_logger PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_logger PUBLIC Threads::Threads)

if(BUILD_TESTING)
    add_executable(logger_tests
        tests/TestDefaultFormatter.cpp
        tests/TestFileOutput.cpp
        tests/TestLogger.cpp
        tests/TestStdErrOutput.cpp
        tests/TestStdOutOutput.cpp
        tests/TestStreamOutput.cpp
        tests/TestSysLogOutput.cpp)
    target_link_libraries(logger_tests PRIVATE libranet::logger GTest::gtest_main)
    gtest_discover_tests(logger_tests)
endif()
