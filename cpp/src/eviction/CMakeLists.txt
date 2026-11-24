add_library(libranet_eviction STATIC
    Evictor.cpp
    Scorer.cpp)
add_library(libranet::eviction ALIAS libranet_eviction)
target_include_directories(libranet_eviction PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_eviction PUBLIC
    libranet::logger
    libranet::msg
    libranet::storage
    libranet::types
    Threads::Threads)

if(BUILD_TESTING)
    add_executable(eviction_tests
        tests/TestEvictor.cpp
        tests/TestScorer.cpp)
    target_link_libraries(eviction_tests PRIVATE libranet::eviction GTest::gtest_main)
    gtest_discover_tests(eviction_tests)
endif()
