#include <exception>
#include <iostream>
#include <string>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "net/Address.hpp"
#include "web/Server.hpp"

using http::Request;
using http::Response;
using http::ResponseCode;
using net::Address;
using std::cerr;
using std::cout;
using std::exception;
using std::string;
using web::Server;

int main() {
    constexpr auto listen_host = "0.0.0.0";
    constexpr auto listen_port = 8086;
    constexpr auto backlog = 128;

    try {
        const Address bind_address(listen_host, listen_port);
        Server server(bind_address);

        server.register_handler("/data",
                                [](const Request& request,
                                   Response& response) {
            response.set_status_code(ResponseCode::Status::Ok);
            response.set_body(string(request.uri()));
        });
        cout << "Starting Libranet web server on port " << listen_port << "...\n";
        server.start(backlog);
    } catch (const exception& e) {
        cerr << "Server error: " << e.what() << '\n';
        return 1;
    }

    return 0;
}