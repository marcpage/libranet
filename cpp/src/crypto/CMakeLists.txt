# Cryptography: hashing, encrypting, signing, and hashing passwords, built on
# OpenSSL, which Conan provides. On macOS, SHA-256, AES, and RSA are built on
# Apple's CommonCrypto and Security frameworks instead.
find_package(OpenSSL REQUIRED)

add_library(libranet_crypto STATIC
    IEncryptKey.cpp
    IHasher.cpp
    IMultiKey.cpp
    IPasswordHasher.cpp
    ISignKey.cpp)
add_library(libranet::crypto ALIAS libranet_crypto)
target_include_directories(libranet_crypto PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_crypto PUBLIC OpenSSL::Crypto)
if(APPLE)
    target_link_libraries(libranet_crypto PUBLIC
        "-framework CoreFoundation"
        "-framework Security")
endif()

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
