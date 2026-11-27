add_library(libranet_config STATIC
    Credential.cpp
    DialOutcomes.cpp
    Guard.cpp
    Service.cpp
)
add_library(libranet::config ALIAS libranet_config)
target_include_directories(libranet_config PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_config PUBLIC
    libranet::crypto
    libranet::http
    libranet::identity
    libranet::io
    libranet::logger
    libranet::msg
    libranet::net
    libranet::peer
    libranet::types
    libranet::web
)

if(BUILD_TESTING)
    add_executable(config_tests
        tests/TestCredential.cpp
        tests/TestDialOutcomes.cpp
        tests/TestGuard.cpp
        tests/TestService.cpp
    )
    target_link_libraries(config_tests PRIVATE libranet::config GTest::gtest_main)
    gtest_discover_tests(config_tests)
endif()
