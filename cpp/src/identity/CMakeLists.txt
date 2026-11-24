add_library(libranet_identity STATIC
    KnownKeys.cpp
    MessageSigner.cpp
    MessageVerifier.cpp
    NodeIdentity.cpp
    SignatureBase.cpp
)
add_library(libranet::identity ALIAS libranet_identity)
target_include_directories(libranet_identity PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_identity PUBLIC
    libranet::crypto
    libranet::http
    libranet::io
    libranet::logger
    libranet::storage
    libranet::types
)

if(BUILD_TESTING)
    add_executable(identity_tests
        tests/TestContentDigest.cpp
        tests/TestKnownKeys.cpp
        tests/TestMessageSigner.cpp
        tests/TestMessageVerifier.cpp
        tests/TestNodeIdentity.cpp
        tests/TestSignatureBase.cpp
    )
    target_link_libraries(identity_tests PRIVATE libranet::identity GTest::gtest_main)
    gtest_discover_tests(identity_tests)
endif()

if(LIBRANET_FUZZERS)
    libranet_add_fuzzer(content_digest_fuzzer fuzz/FuzzContentDigest.cpp libranet::identity)
endif()
