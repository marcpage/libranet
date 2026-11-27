add_library(libranet_config STATIC
    Credential.cpp
)
add_library(libranet::config ALIAS libranet_config)
target_include_directories(libranet_config PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_config PUBLIC
    libranet::crypto
    libranet::io
    libranet::logger
    libranet::types
)

if(BUILD_TESTING)
    add_executable(config_tests
        tests/TestCredential.cpp
    )
    target_link_libraries(config_tests PRIVATE libranet::config GTest::gtest_main)
    gtest_discover_tests(config_tests)
endif()
