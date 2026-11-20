add_library(libranet::io ALIAS libranet_io)
target_include_directories(libranet_io PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_io PUBLIC 
    libranet::net)

if(BUILD_TESTING)
    add_executable(io_tests
        tests/TestBufferedReader.cpp
        tests/TestSocketReader.cpp
        tests/TestMeteredReader.cpp)
    target_link_libraries(io_tests PRIVATE libranet::web GTest::gtest_main)
    gtest_discover_tests(io_tests)
endif()
