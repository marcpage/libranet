# This node's outgoing connections to its peers: for now, the buckets it
# spreads them over.
add_library(libranet_peer STATIC
    Buckets.cpp)
add_library(libranet::peer ALIAS libranet_peer)
target_include_directories(libranet_peer PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_peer PUBLIC libranet::storage)

if(BUILD_TESTING)
    add_executable(peer_tests
        tests/TestBuckets.cpp)
    target_link_libraries(peer_tests PRIVATE libranet::peer GTest::gtest_main)
    gtest_discover_tests(peer_tests)
endif()
