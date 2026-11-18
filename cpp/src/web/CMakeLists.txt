# Messages, and the channels that carry them.
add_library(libranet_web STATIC
    Server.cpp)
add_library(libranet::web ALIAS libranet_web)
target_include_directories(libranet_web PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(libranet_web PUBLIC 
    libranet::net
    libranet::http
    Threads::Threads)
