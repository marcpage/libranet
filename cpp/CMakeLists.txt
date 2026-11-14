cmake_minimum_required(VERSION 3.24)

project(libranet LANGUAGES CXX)

# The node is written to C++17. This is set after project(), so it holds
# whatever compiler.cppstd the Conan profile names.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# compile_commands.json, for clangd and clang-tidy.
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Warnings fail the build of every target. A newer compiler with a new
# warning can still build with `cmake --compile-no-warning-as-error`.
set(CMAKE_COMPILE_WARNING_AS_ERROR ON)

# The GCC-only warnings are guarded: Clang treats a warning option it does
# not know as an error.
if(MSVC)
    add_compile_options(/W4 /permissive-)
else()
    add_compile_options(
        -Wall -Wextra -Wpedantic -Wshadow -Wcast-align -Wcast-qual
        -Wnull-dereference -Wdouble-promotion -Wformat=2 -Wnon-virtual-dtor
        -Wold-style-cast -Woverloaded-virtual -Wconversion -Wsign-conversion
        -Wimplicit-fallthrough -Wsuggest-override -Wextra-semi
        -Wzero-as-null-pointer-constant
        "$<$<CXX_COMPILER_ID:GNU>:-Wduplicated-cond;-Wduplicated-branches;-Wlogical-op>")
endif()

# Coverage: configure a Debug build with -DLIBRANET_COVERAGE=ON, run the
# tests, and gcovr reports which lines they ran. GCC and Clang both write
# the gcov data gcovr reads.
option(LIBRANET_COVERAGE "Instrument the code to measure test coverage" OFF)
if(LIBRANET_COVERAGE)
    if(MSVC)
        message(FATAL_ERROR "LIBRANET_COVERAGE needs GCC or Clang")
    endif()
    add_compile_options(--coverage)
    add_link_options(--coverage)
endif()

find_package(Threads REQUIRED)

# Each library's tests use GoogleTest, which Conan provides. Configure
# with -DBUILD_TESTING=OFF to leave them out.
option(BUILD_TESTING "Build the tests" ON)
if(BUILD_TESTING)
    # A failing test's output is shown when the test target runs CTest, as
    # `conan build` does.
    list(APPEND CMAKE_CTEST_ARGUMENTS --output-on-failure)
    enable_testing()
    find_package(GTest REQUIRED)
    include(GoogleTest)
endif()

# One library per directory under src/, each with its tests in its tests/.
# Headers are included from src/, as in "types/BlockingQueue.hpp".
add_subdirectory(src/types)
add_subdirectory(src/http)
add_subdirectory(src/msg)
add_subdirectory(src/net)

add_executable(libranet src/main.cpp)
