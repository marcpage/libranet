#include "HttpHandler.hpp"

#include <string_view>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"

using http::Request;
using http::Response;
using http::ResponseCode;
using std::string_view;
using storage::IDataSource;
using storage::Identifier;

namespace {

bool handle_search(const Request& request, Response& response) {
    constexpr string_view data_search = "/data/search/";
    const auto uri = request.uri();

    if (uri.rfind(data_search, 0) != 0) {
        return false;
    }

    // const auto search_hash = std::string(uri.substr(data_search.size()));

    response.set_status_code(ResponseCode::Status::Ok);
    response.headers().set("Content-Type", "application/json");
    response.set_body("{\"matches\": []}\r\n"); // Populate with top matching prefix results
    return true;
}

bool no_identity(const Request& request, Response& response) {
    if (request.headers().get("X-Node-Signature").has_value()) {
        return false;
    }

    response.set_status_code(ResponseCode::Status::Unauthorized);
    response.set_body("Unauthorized: Node ID signature missing\r\n");
    return true;
}

bool data_request(const Request& request, Response& response, const IDataSource::Ptr& source) {
    constexpr string_view data_prefix = "/data/";
    const auto uri = request.uri();

    if (uri.rfind(data_prefix, 0) != 0) {
        return false;
    }

    const auto data_id = std::string(uri.substr(data_prefix.size()));
    const auto identifier = Identifier::from(data_id);
    const auto content = source->get(identifier);

    if (!content.has_value()) {
        response.set_status_code(ResponseCode::Status::ServiceUnavailable);
        response.set_body("Service Unavailable: Fetching content from other nodes\r\n");
        return true;
    }

    response.set_status_code(ResponseCode::Status::Ok);
    response.headers().set("Content-Type", "application/octet-stream");
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
    response.set_body(std::string(reinterpret_cast<const char*>(content->data()), content->size()));
    return true;
}

} // namespace

namespace storage {

HttpHandler::HttpHandler(IDataSource::Ptr source)
    : m_source(std::move(source)) {}

void HttpHandler::operator()(const Request& request, Response& response) {
    if (handle_search(request, response)) {
        return;
    }

    if (no_identity(request, response)) {
        return;
    }

    if (data_request(request, response, m_source)) {
        return;
    }

    response.set_status_code(ResponseCode::Status::NotFound);
    response.set_body("Not Found\r\n");
}

} // namespace storage
