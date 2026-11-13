cmake_minimum_required(VERSION 3.24)

project(libranet LANGUAGES CXX)

# The node is written to C++17. This is set after project(), so it holds
# whatever compiler.cppstd the Conan profile names.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# compile_commands.json, for clangd and clang-tidy.
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

add_executable(libranet src/main.cpp)

# Warnings fail the build. A newer compiler with a new warning can still
# build with `cmake --compile-no-warning-as-error`.
set_target_properties(libranet PROPERTIES COMPILE_WARNING_AS_ERROR ON)

# The GCC-only warnings are guarded: Clang treats a warning option it does
# not know as an error.
if(MSVC)
    target_compile_options(libranet PRIVATE /W4 /permissive-)
else()
    target_compile_options(libranet PRIVATE
        -Wall -Wextra -Wpedantic -Wshadow -Wcast-align -Wcast-qual
        -Wnull-dereference -Wdouble-promotion -Wformat=2 -Wnon-virtual-dtor
        -Wold-style-cast -Woverloaded-virtual -Wconversion -Wsign-conversion
        -Wimplicit-fallthrough -Wsuggest-override -Wextra-semi
        -Wzero-as-null-pointer-constant
        "$<$<CXX_COMPILER_ID:GNU>:-Wduplicated-cond;-Wduplicated-branches;-Wlogical-op>")
endif()
