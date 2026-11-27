set(config_page ${PROJECT_SOURCE_DIR}/../applications/config/index.html)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${config_page})
file(READ ${config_page} page_lines)
string(REPLACE "\\" "\\\\" page_lines "${page_lines}")
string(REPLACE "\"" "\\\"" page_lines "${page_lines}")
string(REPLACE "?" "\\?" page_lines "${page_lines}")
string(REPLACE "\r" "\\r" page_lines "${page_lines}")
string(REPLACE "\n" "\\n\",\n    \"" page_lines "${page_lines}")
string(REGEX REPLACE "\",\n    \"$" "" page_lines "${page_lines}")
configure_file(PageHtml.cpp.in PageHtml.cpp @ONLY)

add_library(libranet_config STATIC
    ConnectHandler.cpp
    Credential.cpp
    DialOutcomes.cpp
    Guard.cpp
    NodeHandler.cpp
    NotOffered.cpp
    Page.cpp
    ${CMAKE_CURRENT_BINARY_DIR}/PageHtml.cpp
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
    libranet::protocol
    libranet::storage
    libranet::types
    libranet::web
)

if(BUILD_TESTING)
    add_executable(config_tests
        tests/TestConnectHandler.cpp
        tests/TestCredential.cpp
        tests/TestDialOutcomes.cpp
        tests/TestGuard.cpp
        tests/TestNodeHandler.cpp
        tests/TestNotOffered.cpp
        tests/TestPage.cpp
        tests/TestService.cpp
    )
    target_link_libraries(config_tests PRIVATE libranet::config GTest::gtest_main)
    target_compile_definitions(config_tests PRIVATE "LIBRANET_CONFIG_PAGE=\"${config_page}\"")
    gtest_discover_tests(config_tests)
endif()
