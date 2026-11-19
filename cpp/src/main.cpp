#include <exception>
#include <functional>
#include <iostream>
#include <string>

#include "net/Address.hpp"
#include "storage/HttpHandler.hpp"
#include "storage/Manager.hpp"
#include "web/Server.hpp"

using net::Address;
using std::cerr;
using std::cout;
using std::exception;
using std::string;
using storage::HttpHandler;
using storage::Manager;
using web::Server;

int main() {
    constexpr auto listen_host = "0.0.0.0";
    constexpr auto listen_port = 8086;
    constexpr auto backlog = 128;
    const auto data = Manager::create();
    HttpHandler storage_handler(data);

    data->add("storage");

    try {
        const Address bind_address(listen_host, listen_port);
        Server server(bind_address);

        server.register_handler("/data", std::ref(storage_handler));
        cout << "Starting Libranet web server on port " << listen_port << "...\n";
        server.start(backlog);
    } catch (const exception& e) {
        cerr << "Server error: " << e.what() << '\n';
        return 1;
    }

    return 0;
}