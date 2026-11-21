# The database: statements of SQL, run on SQLite, which Conan provides. Only
# Statement.cpp includes SQLite, so it is linked privately. The tests open
# their own connections to it.
find_package(SQLite3 REQUIRED)

add_library(libranet_db STATIC
    Statement.cpp)
add_library(libranet::db ALIAS libranet_db)
target_include_directories(libranet_db PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_db PRIVATE SQLite::SQLite3)

if(BUILD_TESTING)
    add_executable(db_tests
        tests/TestStatement.cpp)
    target_link_libraries(db_tests PRIVATE libranet::db SQLite::SQLite3 GTest::gtest_main)
    gtest_discover_tests(db_tests)
endif()
