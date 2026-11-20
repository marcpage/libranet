# The lists and answers nodes send each other, read and written as the HTTP
# API gives them. Only headers so far, so an INTERFACE library; it becomes
# STATIC with its first .cpp file.
add_library(libranet_protocol INTERFACE)
add_library(libranet::protocol ALIAS libranet_protocol)
target_include_directories(libranet_protocol INTERFACE ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_protocol
    INTERFACE
        libranet::types
        libranet::logger
        libranet::storage)

if(BUILD_TESTING)
    add_executable(protocol_tests
        tests/TestSeekList.cpp)
    target_link_libraries(protocol_tests PRIVATE libranet::protocol GTest::gtest_main)
    gtest_discover_tests(protocol_tests)
endif()

if(LIBRANET_FUZZERS)
    libranet_add_fuzzer(seek_list_fuzzer fuzz/FuzzSeekList.cpp libranet::protocol)
endif()
