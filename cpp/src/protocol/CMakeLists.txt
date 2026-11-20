# What nodes say to one another over HTTP, read and written. Only headers so
# far, so an INTERFACE library; it becomes STATIC with its first .cpp file.
add_library(libranet_protocol INTERFACE)
add_library(libranet::protocol ALIAS libranet_protocol)
target_include_directories(libranet_protocol INTERFACE ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_protocol
    INTERFACE
        libranet::types
        libranet::http
        libranet::logger
        libranet::storage)

if(BUILD_TESTING)
    add_executable(protocol_tests
        tests/TestSearchResults.cpp)
    target_link_libraries(protocol_tests PRIVATE libranet::protocol GTest::gtest_main)
    gtest_discover_tests(protocol_tests)
endif()

if(LIBRANET_FUZZERS)
    libranet_add_fuzzer(search_results_fuzzer fuzz/FuzzSearchResults.cpp libranet::protocol)
endif()
