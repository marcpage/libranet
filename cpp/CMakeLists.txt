cmake_minimum_required(VERSION 3.23)

project(libranet LANGUAGES CXX)

# The node is written to C++17. This is set after project(), so it holds
# whatever compiler.cppstd the Conan profile names.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(libranet src/main.cpp)

if(MSVC)
    target_compile_options(libranet PRIVATE /W4 /permissive-)
else()
    target_compile_options(libranet PRIVATE -Wall -Wextra -Wpedantic)
endif()
