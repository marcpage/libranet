# The node's identity, and the signatures on what nodes send each other: for
# now, the node's own key and the id other nodes know it by, and the
# signature base RFC 9421 signs.
add_library(libranet_identity STATIC
    NodeIdentity.cpp
    SignatureBase.cpp)
add_library(libranet::identity ALIAS libranet_identity)
target_include_directories(libranet_identity PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_identity PUBLIC
    libranet::types
    libranet::http
    libranet::crypto
    libranet::storage
    libranet::logger)

if(BUILD_TESTING)
    add_executable(identity_tests
        tests/TestNodeIdentity.cpp
        tests/TestSignatureBase.cpp)
    target_link_libraries(identity_tests PRIVATE
        libranet::identity
        libranet::crypto
        GTest::gtest_main)
    gtest_discover_tests(identity_tests)
endif()
