# What the web server serves: for now, the content the node stores, beneath
# /data.
add_library(libranet_api STATIC
    DataHandler.cpp
    NodesHandler.cpp
    SearchHandler.cpp
    SeekHandler.cpp
    Service.cpp
    SignatureGuard.cpp)
add_library(libranet::api ALIAS libranet_api)
target_include_directories(libranet_api PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_api PUBLIC
    libranet::http
    libranet::identity
    libranet::logger
    libranet::msg
    libranet::protocol
    libranet::stats
    libranet::storage
    libranet::types
    libranet::web)

if(BUILD_TESTING)
    add_executable(api_tests
        tests/TestDataHandler.cpp
        tests/TestNodesHandler.cpp
        tests/TestSearchHandler.cpp
        tests/TestSeekHandler.cpp
        tests/TestService.cpp
        tests/TestSignatureGuard.cpp)
    target_link_libraries(api_tests PRIVATE libranet::api GTest::gtest_main)
    gtest_discover_tests(api_tests)
endif()
