# Parsing and generating HTTP. Only headers so far, so an INTERFACE library;
# it becomes STATIC with its first .cpp file.
add_library(libranet_http INTERFACE)
add_library(libranet::http ALIAS libranet_http)
target_include_directories(libranet_http INTERFACE ${PROJECT_SOURCE_DIR}/src)

if(BUILD_TESTING)
    add_executable(http_tests
        TestHeaders.cpp
        TestQuery.cpp
        TestRequest.cpp
        TestResponse.cpp
        TestUrl.cpp)
    target_link_libraries(http_tests PRIVATE libranet::http GTest::gtest_main)
    gtest_discover_tests(http_tests)
endif()
