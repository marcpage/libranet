add_library(libranet_types INTERFACE)
add_library(libranet::types ALIAS libranet_types)
target_include_directories(libranet_types INTERFACE ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_types INTERFACE Threads::Threads)

if(BUILD_TESTING)
    add_executable(types_tests
        tests/TestBlockingQueue.cpp
        tests/TestConverter.cpp
        tests/TestInteger.cpp
        tests/TestJson.cpp
    )
    target_link_libraries(types_tests PRIVATE libranet::types GTest::gtest_main)
    gtest_discover_tests(types_tests)
endif()

if(LIBRANET_FUZZERS)
    libranet_add_fuzzer(json_fuzzer fuzz/FuzzJson.cpp libranet::types)
    libranet_add_fuzzer(integer_fuzzer fuzz/FuzzInteger.cpp libranet::types)
endif()
