#include <iostream>

#include "net/Address.hpp"
#include "web/Server.hpp"

int main() {
    constexpr auto listen_host = "0.0.0.0";
    constexpr auto listen_port = 8086;
    constexpr auto backlog = 128;

    try {
        net::Address bind_address(listen_host, listen_port);
        web::Server server(bind_address);
        std::cout << "Starting Libranet web server on port 8080...\n";
        server.start(backlog);
    } catch (const std::exception& e) {
        std::cerr << "Server error: " << e.what() << '\n';
        return 1;
    }
    return 0;
}