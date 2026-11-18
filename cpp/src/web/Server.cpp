#include "Server.hpp"

#include <atomic>
#include <cstddef>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "http/Headers.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "net/ISocket.hpp"

using http::Headers;
using http::Request;
using http::Response;
using http::ResponseCode;
using net::Address;
using net::ISocket;
using std::atomic;
using std::invalid_argument;
using std::nullopt;
using std::out_of_range;
using std::runtime_error;
using std::size_t;
using std::string;
using std::string_view;
using web::Server;

namespace {

void set_error_response(Response& response, ResponseCode::Status status) {
    const string message(ResponseCode::default_reason_phrase(status));

    response.set_status_code(status);
    response.set_body(message + "\r\n");
}

void file_not_found([[maybe_unused]] const Request& request, Response& response) {
    set_error_response(response, ResponseCode::Status::NotFound);
}

string read_body(ISocket::Ptr& client, size_t bytes) {
    const auto body = client->read_exact(bytes);

    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
    return string(reinterpret_cast<const char*>(body.data()), body.size());
}

std::optional<Request> read(ISocket::Ptr& client, const atomic<bool>& running) {
    Request request;
    string line;
    string last_key;
    size_t body_length = 0;

    if (!client->read_line(line) || !request.parse_request_line(line)) {
        return nullopt;
    }

    while (running && client->read_line(line)) {
        if (line.empty()) {
            break;
        }

        last_key = Headers::process_header_line(line, last_key, request.headers());
    }

    const auto content_length = request.headers().get("Content-Length");

    if (content_length.has_value()) {
        try {
            body_length = std::stoull(std::string(content_length.value()));
        } catch (const invalid_argument&) {
            return nullopt;
        } catch (const out_of_range&) {
            return nullopt;
        }
    }

    request.set_body(read_body(client, body_length));
    return request;
}

} // namespace

namespace web {

Server::Server(Address address) : m_address(std::move(address)) {
    m_listener = net::ISocket::bind(m_address);
}

void Server::register_handler(string_view path, RequestHandler handler) {
    m_handlers[string(path)] = std::move(handler);
}

void Server::start(int backlog) {
    if (!m_listener) {
        throw runtime_error("Failed to bind socket on the given address.");
    }

    m_listener->listen(backlog);
    m_running = true;

    while (m_running) {
        auto client = m_listener->accept();

        if (!client || !client->is_open()) {
            continue;
        }

        const auto request = read(client, m_running);

        if (!request.has_value()) {
            Response bad_req;

            set_error_response(bad_req, ResponseCode::Status::BadRequest);
            client->send_all(bad_req.to_string());
            client->close();
            continue;
        }

        Response response;

        _find_handler(request->uri())(*request, response);
        client->send_all(response.to_string());
        client->close();
    }
}

void Server::stop() {
    m_running = false;

    if (m_listener) {
        m_listener->close();
    }
}

Server::RequestHandler Server::_find_handler(string_view uri) const {
    const string uri_str(uri);
    const auto found_handler = m_handlers.find(uri_str);

    if (found_handler != m_handlers.end()) {
        return found_handler->second;
    }

    string best_prefix;
    RequestHandler best_handler = file_not_found;

    for (const auto& [prefix, handler] : m_handlers) {
        if (uri_str.rfind(prefix, 0) == 0 && prefix.size() > best_prefix.size()) {
            best_prefix = prefix;
            best_handler = handler;
        }
    }

    return best_handler;
}

} // namespace web