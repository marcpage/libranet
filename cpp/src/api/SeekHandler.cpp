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
#include "types/Constants.hpp"
#include "web/ConnectionInfo.hpp"

using http::Problem;
using http::Request;
using http::Response;
using http::ResponseCode;
using protocol::SeekList;
using std::invalid_argument;
using std::mutex;
using std::nullopt;
using std::optional;
using std::scoped_lock;
using std::size_t;
using std::string;
using std::string_view;
using types::json_content_type;
using types::seek_path;
using web::ConnectionInfo;

namespace {

using Status = ResponseCode::Status;

// The methods /data/seek takes, as Allow names them.
constexpr string_view allowed_methods = "GET, POST";

// Whether uri's path, less any query, is /data/seek.
bool is_seek_path(string_view uri) {
    return seek_path == uri.substr(0, uri.find('?'));
}

// The list as it is sent, in fewer than limit bytes, as text.
string body_of(const SeekList& list, size_t limit) {
    return list.body(limit).text;
}

// The list the body of request, on connection, holds, decompressed to no
// more than list_bytes; or nothing when it holds none, having answered 400
// saying why (HttpApi §10.5).
optional<SeekList> posted_list(const Request& request, size_t list_bytes, Response& response,
                               const ConnectionInfo& connection) {
    try {
        return SeekList::from_body(request.body(), list_bytes);
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

// The list the node seeks, as it is sent: body, which guard guards.
void answer_get(mutex& guard, const string& body, Response& response) {
    const scoped_lock lock(guard);

    response.set_status_code(Status::Ok);
    response.headers().set("Content-Type", string(json_content_type));
    response.set_body(body);
}

// Takes what a peer seeks, which nothing here acts on yet, so that the peer
// is answered as the specification asks (HttpApi §10.7). The list is
// decompressed to no more than list_bytes.
void answer_post(size_t list_bytes, const Request& request, Response& response,
                 const ConnectionInfo& connection) {
    const auto list = posted_list(request, list_bytes, response, connection);

    if (!list) {
        return;
    }

    lInfo << sender_of(connection) << "seeks" << list->data().size() << "identifiers and"
          << list->search().size() << "prefixes";
    response.set_status_code(Status::Accepted);
}

} // namespace

namespace api {

SeekHandler::SeekHandler(size_t list_bytes, size_t served_bytes)
    : m_list_bytes(list_bytes)
    , m_served_bytes(served_bytes)
    , m_body(body_of(SeekList(), m_served_bytes)) {}

void SeekHandler::set_list(const SeekList& new_list) {
    auto body = body_of(new_list, m_served_bytes);
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
        answer_get(m_mutex, m_body, response);
    } else if ("POST" == method) {
        answer_post(m_list_bytes, request, response, connection);
    } else {
        Problem::answer_method_not_allowed(request, allowed_methods, response);
    }
}

} // namespace api
