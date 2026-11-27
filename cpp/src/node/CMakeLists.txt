find_package(yaml-cpp REQUIRED)

add_library(libranet_node STATIC
    Node.cpp
    SettingsFile.cpp
)
add_library(libranet::node ALIAS libranet_node)
target_include_directories(libranet_node PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_node
    PUBLIC
        libranet::api
        libranet::config
        libranet::eviction
        libranet::http
        libranet::identity
        libranet::io
        libranet::logger
        libranet::net
        libranet::peer
        libranet::protocol
        libranet::stats
        libranet::storage
        libranet::types
    PRIVATE
        yaml-cpp::yaml-cpp
)

if(BUILD_TESTING)
    add_executable(node_tests
        tests/TestNode.cpp
        tests/TestSettingsFile.cpp
    )
    target_link_libraries(node_tests PRIVATE libranet::node GTest::gtest_main)
    gtest_discover_tests(node_tests)
endif()

if(LIBRANET_FUZZERS)
    libranet_add_fuzzer(settings_file_fuzzer fuzz/FuzzSettingsFile.cpp libranet::node)
endif()
