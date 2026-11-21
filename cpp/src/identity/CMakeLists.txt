# What a node signs with, and checks others' signatures by: for now, the
# Content-Digest that binds a signature to a body. Only headers so far, so an
# INTERFACE library; it becomes STATIC with its first .cpp file.
add_library(libranet_identity INTERFACE)
add_library(libranet::identity ALIAS libranet_identity)
target_include_directories(libranet_identity INTERFACE ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_identity INTERFACE libranet::http libranet::crypto)

if(BUILD_TESTING)
    add_executable(identity_tests
        tests/TestContentDigest.cpp)
    target_link_libraries(identity_tests PRIVATE libranet::identity GTest::gtest_main)
    gtest_discover_tests(identity_tests)
endif()

if(LIBRANET_FUZZERS)
    libranet_add_fuzzer(content_digest_fuzzer fuzz/FuzzContentDigest.cpp libranet::identity)
endif()
