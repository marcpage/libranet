add_library(libranet_net STATIC
    AddressPosix.cpp
    IpAddressPosix.cpp
    ISocket.cpp
    SocketPosix.cpp
)
add_library(libranet::net ALIAS libranet_net)
target_include_directories(libranet_net PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_net PUBLIC libranet::types)

if(BUILD_TESTING)
    add_executable(net_tests
        tests/TestAddress.cpp
        tests/TestIpAddress.cpp
        tests/TestISocket.cpp
        tests/TestSocketPosix.cpp
    )
    target_link_libraries(net_tests PRIVATE libranet::net GTest::gtest_main)
    gtest_discover_tests(net_tests)
endif()

if(LIBRANET_FUZZERS)
    libranet_add_fuzzer(address_fuzzer fuzz/FuzzAddress.cpp libranet::net)
    libranet_add_fuzzer(ip_address_fuzzer fuzz/FuzzIpAddress.cpp libranet::net)
endif()
