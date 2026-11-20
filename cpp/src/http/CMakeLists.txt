# Parsing and generating HTTP. Only headers so far, so an INTERFACE library;
# it becomes STATIC with its first .cpp file.
add_library(libranet_http INTERFACE)
add_library(libranet::http ALIAS libranet_http)
target_include_directories(libranet_http INTERFACE ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_http INTERFACE libranet::types)

if(BUILD_TESTING)
    add_executable(http_tests
        tests/TestHeaders.cpp
        tests/TestProblem.cpp
        tests/TestQuery.cpp
        tests/TestRequest.cpp
        tests/TestResponse.cpp
        tests/TestResponseCode.cpp
        tests/TestUrl.cpp)
    target_link_libraries(http_tests PRIVATE libranet::http GTest::gtest_main)
    gtest_discover_tests(http_tests)
endif()

if(LIBRANET_FUZZERS)
    libranet_add_fuzzer(url_fuzzer fuzz/FuzzUrl.cpp libranet::http)
    libranet_add_fuzzer(query_fuzzer fuzz/FuzzQuery.cpp libranet::http)
    libranet_add_fuzzer(headers_fuzzer fuzz/FuzzHeaders.cpp libranet::http)
    libranet_add_fuzzer(request_fuzzer fuzz/FuzzRequest.cpp libranet::http)
    libranet_add_fuzzer(response_fuzzer fuzz/FuzzResponse.cpp libranet::http)
endif()
