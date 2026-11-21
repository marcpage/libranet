# The node's identity: its own key, and the id other nodes know it by.
add_library(libranet_identity STATIC
    NodeIdentity.cpp)
add_library(libranet::identity ALIAS libranet_identity)
target_include_directories(libranet_identity PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_identity PUBLIC
    libranet::types
    libranet::crypto
    libranet::storage
    libranet::logger)

if(BUILD_TESTING)
    add_executable(identity_tests
        tests/TestNodeIdentity.cpp)
    target_link_libraries(identity_tests PRIVATE libranet::identity GTest::gtest_main)
    gtest_discover_tests(identity_tests)
endif()
