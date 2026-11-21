add_library(libranet_peer STATIC
    Buckets.cpp
    DialPlan.cpp
    PeerConnection.cpp
)
add_library(libranet::peer ALIAS libranet_peer)
target_include_directories(libranet_peer PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_peer PUBLIC
    libranet::storage
    libranet::http
    libranet::io
    libranet::net
    libranet::stats
    libranet::storage
    Threads::Threads
)

if(BUILD_TESTING)
    add_executable(peer_tests
        tests/TestBuckets.cpp
        tests/TestDialPlan.cpp
        tests/TestPeerConnection.cpp
    )
    target_link_libraries(peer_tests PRIVATE libranet::peer GTest::gtest_main)
    gtest_discover_tests(peer_tests)
endif()
