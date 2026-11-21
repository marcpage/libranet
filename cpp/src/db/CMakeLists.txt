# A connection to an SQLite database, built on SQLite, which Conan provides.
# Only Database.cpp includes it, so it is linked privately.
find_package(SQLite3 REQUIRED)

add_library(libranet_db STATIC
    Database.cpp)
add_library(libranet::db ALIAS libranet_db)
target_include_directories(libranet_db PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_db PRIVATE SQLite::SQLite3)

if(BUILD_TESTING)
    add_executable(db_tests
        tests/TestDatabase.cpp)
    target_link_libraries(db_tests PRIVATE libranet::db GTest::gtest_main)
    gtest_discover_tests(db_tests)
endif()
