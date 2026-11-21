# The node's identity, and the signatures on what nodes send each other: for
# now, the signature base RFC 9421 signs, and the public keys of other nodes,
# read from the store.
add_library(libranet_identity STATIC
    KnownKeys.cpp
    SignatureBase.cpp)
add_library(libranet::identity ALIAS libranet_identity)
target_include_directories(libranet_identity PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_identity PUBLIC
    libranet::types
    libranet::http
    libranet::crypto
    libranet::storage)

if(BUILD_TESTING)
    add_executable(identity_tests
        tests/TestKnownKeys.cpp
        tests/TestSignatureBase.cpp)
    target_link_libraries(identity_tests PRIVATE
        libranet::identity
        libranet::crypto
        GTest::gtest_main)
    gtest_discover_tests(identity_tests)
endif()
