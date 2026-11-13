# Addresses and sockets, on POSIX systems.
add_library(libranet_net STATIC
    AddressPosix.cpp
    ISocket.cpp
    SocketPosix.cpp)
add_library(libranet::net ALIAS libranet_net)
target_include_directories(libranet_net PUBLIC ${PROJECT_SOURCE_DIR}/src)

if(BUILD_TESTING)
    add_executable(net_tests
        TestAddress.cpp
        TestSocketPosix.cpp)
    target_link_libraries(net_tests PRIVATE libranet::net GTest::gtest_main)
    gtest_discover_tests(net_tests)
endif()
