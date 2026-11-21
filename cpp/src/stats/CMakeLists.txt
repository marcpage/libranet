add_library(libranet_stats STATIC
    NodeAddresses.cpp)
add_library(libranet::stats ALIAS libranet_stats)
target_include_directories(libranet_stats PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_stats PUBLIC
    libranet::db
    libranet::protocol
    libranet::storage)

if(BUILD_TESTING)
    add_executable(stats_tests
        tests/TestNodeAddresses.cpp)
    target_link_libraries(stats_tests PRIVATE libranet::stats GTest::gtest_main)
    gtest_discover_tests(stats_tests)
endif()
