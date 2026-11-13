# Messages, and the channels that carry them.
add_library(libranet_msg STATIC
    AsyncChannel.cpp
    Channel.cpp)
add_library(libranet::msg ALIAS libranet_msg)
target_include_directories(libranet_msg PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_msg PUBLIC libranet::types Threads::Threads)

if(BUILD_TESTING)
    add_executable(msg_tests
        TestAsyncChannel.cpp
        TestAsyncChannelOf.cpp
        TestChannel.cpp
        TestMessage.cpp)
    target_link_libraries(msg_tests PRIVATE libranet::msg GTest::gtest_main)
    gtest_discover_tests(msg_tests)
endif()
