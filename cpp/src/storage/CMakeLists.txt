# Messages, and the channels that carry them.
add_library(libranet_storage STATIC
    HttpHandler.cpp
    Manager.cpp)
add_library(libranet::storage ALIAS libranet_storage)
target_include_directories(libranet_storage PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_storage PUBLIC 
    libranet::http
    libranet::crypto)

if(BUILD_TESTING)
    add_executable(storage_tests
        tests/TestDataDir.cpp
        tests/TestHttpHandler.cpp
        tests/TestIdentifier.cpp
        tests/TestManager.cpp)
    target_link_libraries(storage_tests PRIVATE libranet::storage GTest::gtest_main)
    gtest_discover_tests(storage_tests)
endif()
