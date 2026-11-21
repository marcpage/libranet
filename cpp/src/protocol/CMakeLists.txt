# What nodes send each other, read and written: for now, their node lists
# and seek lists.
add_library(libranet_protocol STATIC
    NodeList.cpp)
add_library(libranet::protocol ALIAS libranet_protocol)
target_include_directories(libranet_protocol PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_protocol PUBLIC
    libranet::types
    libranet::http
    libranet::net
    libranet::storage
    libranet::logger)

if(BUILD_TESTING)
    add_executable(protocol_tests
        tests/TestNodeList.cpp
        tests/TestSeekList.cpp)
    target_link_libraries(protocol_tests PRIVATE libranet::protocol GTest::gtest_main)
    gtest_discover_tests(protocol_tests)
endif()

if(LIBRANET_FUZZERS)
    libranet_add_fuzzer(node_list_fuzzer fuzz/FuzzNodeList.cpp libranet::protocol)
    libranet_add_fuzzer(seek_list_fuzzer fuzz/FuzzSeekList.cpp libranet::protocol)
endif()
