# Reading bytes: from a socket, through a buffer, and up to a limit. Only
# headers so far, so an INTERFACE library; it becomes STATIC with its first
# .cpp file.
add_library(libranet_io INTERFACE)
add_library(libranet::io ALIAS libranet_io)
target_include_directories(libranet_io INTERFACE ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_io INTERFACE libranet::net)

if(BUILD_TESTING)
    add_executable(io_tests
        tests/TestBufferedReader.cpp
        tests/TestMeteredReader.cpp
        tests/TestSocketReader.cpp)
    target_link_libraries(io_tests PRIVATE libranet::io GTest::gtest_main)
    gtest_discover_tests(io_tests)
endif()
