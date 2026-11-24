add_library(libranet_peer STATIC
    Buckets.cpp
    Connections.cpp
    DialPlan.cpp
    Fetch.cpp
    FirstContact.cpp
    HandOff.cpp
    NodeListPublisher.cpp
    PeerConnection.cpp
    PeerSearch.cpp
    PeerSession.cpp
    Pusher.cpp
    SeekFulfiller.cpp
    Seeker.cpp
)
add_library(libranet::peer ALIAS libranet_peer)
target_include_directories(libranet_peer PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_peer PUBLIC
    libranet::http
    libranet::identity
    libranet::io
    libranet::logger
    libranet::msg
    libranet::net
    libranet::protocol
    libranet::stats
    libranet::storage
    libranet::types
    Threads::Threads
)

if(BUILD_TESTING)
    add_executable(peer_tests
        tests/TestBuckets.cpp
        tests/TestConnections.cpp
        tests/TestDialPlan.cpp
        tests/TestFetch.cpp
        tests/TestFirstContact.cpp
        tests/TestHandOff.cpp
        tests/TestNodeListPublisher.cpp
        tests/TestPeerConnection.cpp
        tests/TestPeerSearch.cpp
        tests/TestPeerSession.cpp
        tests/TestPusher.cpp
        tests/TestSeekFulfiller.cpp
        tests/TestSeeker.cpp
    )
    target_link_libraries(peer_tests PRIVATE libranet::peer GTest::gtest_main)
    gtest_discover_tests(peer_tests)
endif()
