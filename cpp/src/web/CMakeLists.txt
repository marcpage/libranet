add_library(libranet_web STATIC
    Server.cpp
)
add_library(libranet::web ALIAS libranet_web)
target_include_directories(libranet_web PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_web PUBLIC 
    libranet::net
    libranet::io
    libranet::http
    libranet::logger
    libranet::storage
    libranet::types
    Threads::Threads
)

if(BUILD_TESTING)
    add_executable(web_tests
        tests/TestConnectionInfo.cpp
        tests/TestServer.cpp
    )
    target_link_libraries(web_tests PRIVATE libranet::web GTest::gtest_main)
    gtest_discover_tests(web_tests)
endif()
