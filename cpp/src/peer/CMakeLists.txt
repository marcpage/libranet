add_library(libranet_peer STATIC
    Buckets.cpp
    DialPlan.cpp
    Fetch.cpp
    FirstContact.cpp
    NodeListPublisher.cpp
    PeerConnection.cpp
    PeerSearch.cpp
    PeerSession.cpp
)
add_library(libranet::peer ALIAS libranet_peer)
target_include_directories(libranet_peer PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_peer PUBLIC
    libranet::storage
    libranet::http
    libranet::identity
    libranet::io
    libranet::logger
    libranet::msg
    libranet::net
    libranet::protocol
    libranet::stats
    libranet::storage
    Threads::Threads
)

if(BUILD_TESTING)
    add_executable(peer_tests
        tests/TestBuckets.cpp
        tests/TestDialPlan.cpp
        tests/TestFetch.cpp
        tests/TestFirstContact.cpp
        tests/TestNodeListPublisher.cpp
        tests/TestPeerConnection.cpp
        tests/TestPeerSearch.cpp
        tests/TestPeerSession.cpp
    )
    target_link_libraries(peer_tests PRIVATE libranet::peer GTest::gtest_main)
    gtest_discover_tests(peer_tests)
endif()
