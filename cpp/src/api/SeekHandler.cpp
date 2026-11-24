#include "SeekHandler.hpp"

#include <cstddef>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "logger/Logger.hpp"
#include "protocol/SeekList.hpp"
#include "types/Converter.hpp"
#include "web/ConnectionInfo.hpp"

using http::Problem;
using http::Request;
using http::Response;
using http::ResponseCode;
using protocol::SeekList;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::scoped_lock;
using std::size_t;
using std::string;
using std::string_view;
using types::Converter;
using web::ConnectionInfo;

namespace {

using Status = ResponseCode::Status;

constexpr string_view seek_path = "/data/seek";

// The methods /data/seek takes, as Allow names them.
constexpr string_view allowed_methods = "GET, POST";

// What a list is served as, compressed or not, as the Python node serves it.
constexpr string_view json_content_type = "application/json";

// Whether uri's path, less any query, is /data/seek.
bool is_seek_path(string_view uri) {
    return seek_path == uri.substr(0, uri.find('?'));
}

// The list as it is sent, as text.
string body_of(const SeekList& list) {
    return Converter::text_of(list.write());
}

// The list the body of request, on connection, holds, decompressed to no
// more than list_bytes; or nothing when it holds none, having answered 400
// saying why (HttpApi §10.5).
optional<SeekList> posted_list(const Request& request, size_t list_bytes, Response& response,
                               const ConnectionInfo& connection) {
    try {
        return SeekList::read(Converter::bytes_of(request.body()), list_bytes);
    } catch (const invalid_argument& error) {
        lDebug << "Refused the seek list from" << connection.remote_address().to_string() + ":"
               << error.what();
        Problem(Problem::Type::InvalidList, Status::BadRequest, error.what()).answer(response);
        return nullopt;
    }
}

// The node whose signature the connection carries, or, when it carries
// none, the address it came from.
string sender_of(const ConnectionInfo& connection) {
    if (const auto& node = connection.node()) {
        return node->to_string();
    }

    return connection.remote_address().to_string();
}

} // namespace

namespace api {

SeekHandler::SeekHandler(size_t list_bytes)
    : m_list_bytes(list_bytes)
    , m_body(body_of(SeekList())) {}

void SeekHandler::set_list(const SeekList& new_list) {
    auto body = body_of(new_list);
    const scoped_lock lock(m_mutex);

    m_body = std::move(body);
}

void SeekHandler::operator()(const Request& request, Response& response,
                             const ConnectionInfo& connection) const {
    if (!is_seek_path(request.uri())) {
        Problem(Status::NotFound, "No resource exists at this path.").answer(response);
        return;
    }

    const auto method = request.method();

    if ("GET" == method) {
        _get(response);
    } else if ("POST" == method) {
        _post(request, response, connection);
    } else {
        Problem::answer_method_not_allowed(request, allowed_methods, response);
    }
}

void SeekHandler::_get(Response& response) const {
    const scoped_lock lock(m_mutex);

    response.set_status_code(Status::Ok);
    response.headers().set("Content-Type", string(json_content_type));
    response.set_body(m_body);
}

// Takes what a peer seeks, which nothing here acts on yet, so that the peer
// is answered as the specification asks (HttpApi §10.7).
void SeekHandler::_post(const Request& request, Response& response,
                        const ConnectionInfo& connection) const {
    const auto list = posted_list(request, m_list_bytes, response, connection);

    if (!list) {
        return;
    }

    lInfo << sender_of(connection) << "seeks" << list->data().size() << "identifiers and"
          << list->search().size() << "prefixes";
    response.set_status_code(Status::Accepted);
}

} // namespace api
