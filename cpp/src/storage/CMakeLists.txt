# Compressing and decompressing is built on zlib, which Conan provides. Only
# Zlib.cpp includes it, so it is linked privately. ZLIB_CONST has zlib take
# the bytes it reads as const.
find_package(ZLIB REQUIRED)

add_library(libranet_storage STATIC
    Manager.cpp
    SearchAnswers.cpp
    Zlib.cpp)
add_library(libranet::storage ALIAS libranet_storage)
target_include_directories(libranet_storage PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_storage
    PUBLIC
        libranet::http
        libranet::crypto
        libranet::logger
    PRIVATE
        ZLIB::ZLIB)
target_compile_definitions(libranet_storage PRIVATE ZLIB_CONST)

if(BUILD_TESTING)
    add_executable(storage_tests
        tests/TestDataDir.cpp
        tests/TestDataNotFound.cpp
        tests/TestDataStored.cpp
        tests/TestHashPrefix.cpp
        tests/TestIdentifier.cpp
        tests/TestManager.cpp
        tests/TestSearchAnswers.cpp
        tests/TestZlib.cpp)
    target_link_libraries(storage_tests PRIVATE libranet::storage GTest::gtest_main)
    gtest_discover_tests(storage_tests)
endif()

if(LIBRANET_FUZZERS)
    libranet_add_fuzzer(zlib_fuzzer fuzz/FuzzZlib.cpp libranet::storage)
endif()
