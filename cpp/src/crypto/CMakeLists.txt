# Cryptography: hashing, encrypting, signing, and hashing passwords. SHA-256,
# AES, and RSA are built on Apple's CommonCrypto and Security frameworks,
# and Ed25519 and Argon2id on OpenSSL, which Conan provides.
find_package(OpenSSL REQUIRED)

add_library(libranet_crypto STATIC
    IEncryptKey.cpp
    IHasher.cpp
    IMultiKey.cpp
    IPasswordHasher.cpp
    ISignKey.cpp)
add_library(libranet::crypto ALIAS libranet_crypto)
target_include_directories(libranet_crypto PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_crypto PUBLIC
    OpenSSL::Crypto
    "-framework CoreFoundation"
    "-framework Security")

if(BUILD_TESTING)
    add_executable(crypto_tests
        tests/TestBase64.cpp
        tests/TestIEncryptKey.cpp
        tests/TestIHasher.cpp
        tests/TestIMultiKey.cpp
        tests/TestIPasswordHasher.cpp
        tests/TestISignKey.cpp
        tests/TestPem.cpp)
    target_link_libraries(crypto_tests PRIVATE libranet::crypto GTest::gtest_main)
    gtest_discover_tests(crypto_tests)
endif()
