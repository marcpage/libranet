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

# In Debug builds, the ones the tests run in, the C++ standard library
# checks bounds and preconditions, such as an index past a vector's size or
# reading an empty optional, and stops the program when one fails. Each
# library reads only its own setting: _GLIBCXX_ASSERTIONS for GCC's
# libstdc++, _LIBCPP_HARDENING_MODE for LLVM's libc++, which Apple's Clang
# uses. Neither changes the library's ABI, so prebuilt GoogleTest still
# links.
add_compile_definitions(
    "$<$<CONFIG:Debug>:_GLIBCXX_ASSERTIONS>"
    "$<$<CONFIG:Debug>:_LIBCPP_HARDENING_MODE=_LIBCPP_HARDENING_MODE_EXTENSIVE>")

# Coverage: configure a Debug build with -DLIBRANET_COVERAGE=ON, run the
# tests, and gcovr reports which lines they ran. GCC and Clang both write
# the gcov data gcovr reads. The tests run code from many threads at once,
# and the counters are not updated atomically unless asked: GCC only does
# so when compiling with -pthread, which CMake leaves out where the C
# library has the threads. Counts lost to threads racing make gcov work out
# negative ones from the rest, and gcovr stops.
option(LIBRANET_COVERAGE "Instrument the code to measure test coverage" OFF)
if(LIBRANET_COVERAGE)
    if(MSVC)
        message(FATAL_ERROR "LIBRANET_COVERAGE needs GCC or Clang")
    endif()
    add_compile_options(--coverage -fprofile-update=atomic)
    add_link_options(--coverage)
endif()

# Sanitizers: configure a Debug build with LIBRANET_SANITIZERS set to what
# -fsanitize= takes, such as "address,undefined" or "thread", and run the
# tests. A finding fails the test it is in: AddressSanitizer and,
# with -fno-sanitize-recover, UndefinedBehaviorSanitizer stop at the first,
# and ThreadSanitizer exits with an error status.
set(LIBRANET_SANITIZERS "" CACHE STRING
    "Sanitizers to build with, as -fsanitize= takes them; empty for none")
if(LIBRANET_SANITIZERS)
    if(MSVC)
        message(FATAL_ERROR "LIBRANET_SANITIZERS needs GCC or Clang")
    endif()
    add_compile_options(
        -fsanitize=${LIBRANET_SANITIZERS} -fno-sanitize-recover=all
        -fno-omit-frame-pointer)
    add_link_options(-fsanitize=${LIBRANET_SANITIZERS})
endif()

# Fuzzers: configure with -DLIBRANET_FUZZERS=ON to build a libFuzzer program
# for each parser, from each library's fuzz/ directory. libFuzzer feeds the
# parser inputs it makes up, keeping those that reach new code; built with
# LIBRANET_SANITIZERS=address,undefined, it stops at the first fault.
# libFuzzer comes with LLVM's Clang, but not with Apple's.
option(LIBRANET_FUZZERS "Build a libFuzzer program for each parser" OFF)
if(LIBRANET_FUZZERS AND NOT CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
    message(FATAL_ERROR "LIBRANET_FUZZERS needs LLVM's Clang")
endif()

# A fuzzer: one source file, with the libraries it uses.
function(libranet_add_fuzzer name source)
    add_executable(${name} ${source})
    target_link_libraries(${name} PRIVATE ${ARGN})
    target_compile_options(${name} PRIVATE -fsanitize=fuzzer)
    target_link_options(${name} PRIVATE -fsanitize=fuzzer)
endfunction()

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
add_subdirectory(src/io)
add_subdirectory(src/web)
add_subdirectory(src/crypto)
add_subdirectory(src/storage)
add_subdirectory(src/protocol)
add_subdirectory(src/api)
add_subdirectory(src/node)
add_subdirectory(src/logger)
add_subdirectory(src/db)

add_executable(libranet src/main.cpp)
target_link_libraries(libranet PRIVATE libranet::node)
