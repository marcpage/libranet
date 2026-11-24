add_library(libranet_io STATIC
    Directories.cpp
    SafeWriteFile.cpp
)
add_library(libranet::io ALIAS libranet_io)
target_include_directories(libranet_io PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_io PUBLIC libranet::net)

if(BUILD_TESTING)
    add_executable(io_tests
        tests/TestBufferedReader.cpp
        tests/TestMeteredReader.cpp
        tests/TestSafeWriteFile.cpp
        tests/TestSocketReader.cpp
    )
    target_link_libraries(io_tests PRIVATE libranet::io GTest::gtest_main)
    gtest_discover_tests(io_tests)
endif()
