#include "DataHandler.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cstddef>
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
#include "msg/Channel.hpp"
#include "storage/DataNotFound.hpp"
#include "storage/DataRequested.hpp"
#include "storage/DataStored.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"
#include "web/ConnectionInfo.hpp"

using http::Problem;
using http::Request;
using http::Response;
using http::ResponseCode;
using msg::Channel;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::size_t;
using std::string;
using std::string_view;
using std::to_string;
using storage::DataNotFound;
using storage::DataRequested;
using storage::DataStored;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;
using types::data_path_prefix;
using types::octet_stream_content_type;
using web::ConnectionInfo;
using namespace std::string_view_literals;

namespace {

using IsHeldOff = api::DataHandler::IsHeldOff;
using Milliseconds = api::DataHandler::Milliseconds;
using Status = ResponseCode::Status;

// The algorithm and hash a path names, not yet checked.
struct DataPath {
    string algorithm;
    string hash;
};

// The methods /data/{algorithm}/{hash} takes, as Allow names them.
constexpr string_view allowed_methods = "GET, PUT";

// The names of the endpoints beneath /data, which are never hash algorithms
// (HttpApi §5), as the Python node lists them.
constexpr std::array endpoint_names{
    "search"sv,
    "nodes"sv,
    "seek"sv,
    "client"sv,
    "directory"sv,
    "imports"sv,
    "bundles"sv,
    "store"sv,
    "applications"sv,
    "signed"sv,
    "drop"sv,
    "encrypted"sv,
    "users"sv,
    "fallback"sv,
    "session"sv,
    "peers"sv,
    "trust"sv,
    "archives"sv,
    "karma"sv,
    "chaos"sv,
    "zip"sv,
};

// What uri names, when its path, less any query, is
// /data/{algorithm}/{hash}, each part not empty, and the algorithm is not
// another endpoint's name; nothing otherwise.
optional<DataPath> data_path(string_view uri) {
    const auto path = uri.substr(0, uri.find('?'));

    if (path.substr(0, data_path_prefix.size()) != data_path_prefix) {
        return nullopt;
    }

    const auto rest = path.substr(data_path_prefix.size());
    const auto slash = rest.find('/');

    if (0 == slash || string_view::npos == slash) {
        return nullopt;
    }

    const auto algorithm = rest.substr(0, slash);
    const auto hash = rest.substr(slash + 1);

    if (hash.empty() || string_view::npos != hash.find('/')
        || std::find(endpoint_names.begin(), endpoint_names.end(), algorithm)
               != endpoint_names.end()) {
        return nullopt;
    }

    return DataPath{string(algorithm), string(hash)};
}

// The identifier path, of request on connection, names, or nothing when it
// names none that is valid, having answered 400 saying why (HttpApi §5.4).
optional<Identifier> identifier_of(DataPath path, const Request& request, Response& response,
                                   const ConnectionInfo& connection) {
    try {
        return Identifier(std::move(path.algorithm), std::move(path.hash));
    } catch (const invalid_argument& error) {
        lDebug << "Refused" << request.method() << request.uri() << "from"
               << connection.remote_address().to_string() + ":" << error.what();
        Problem(Problem::Type::InvalidContentAddress, Status::BadRequest, error.what())
            .answer(response);
        return nullopt;
    }
}

// retry_after, once checked, as the Python node checks it.
Milliseconds checked(Milliseconds retry_after) {
    if (retry_after < Milliseconds::zero()) {
        throw invalid_argument("network.retry_after_seconds is less than zero");
    }

    return retry_after;
}

// body_bytes, once checked, as the Python node checks it, though at least
// as large as an object the protocol allows.
size_t checked_body_bytes(size_t body_bytes) {
    if (0 == body_bytes || body_bytes > api::DataHandler::max_body_bytes) {
        throw invalid_argument("storage.max_object_bytes is not from 1 to "
                               + to_string(api::DataHandler::max_body_bytes));
    }

    return body_bytes;
}

// Answers 503 for content not held yet, which the node looks for, telling
// the client to ask again after retry_after (HttpApi §5.2), and that the
// answer is not to be kept, as a retry is meant to find the content
// (HttpApi §20). The problem's retry_after repeats Retry-After (HttpApi
// §17.2). Each carries whole seconds, so retry_after is rounded up to the
// second.
void answer_unavailable(Milliseconds retry_after, Response& response) {
    const Problem problem(Problem::Type::ContentUnavailable, Status::ServiceUnavailable,
                          "The content is not stored here yet; it is being looked for.");
    auto body = problem.to_json();
    const auto seconds = std::chrono::ceil<std::chrono::seconds>(retry_after).count();

    body["retry_after"] = seconds;
    problem.answer(response);
    response.set_body(body.dump());
    response.headers().set("Retry-After", to_string(seconds));
    response.headers().set("Cache-Control", "no-store");
}

// Answers 413 for a body of size bytes, larger than the limit a PUT may
// have.
void answer_too_large(size_t size, size_t limit, Response& response) {
    Problem(Problem::Type::ContentTooLarge, Status::PayloadTooLarge,
            "Request bodies are limited to " + to_string(limit) + " bytes; this one has "
                + to_string(size) + ".")
        .answer(response);
}

// Answers 400 for a body that is not the content identifier names, as it
// is, nor once decompressed to no more than limit bytes.
void answer_not_content(const Identifier& identifier, size_t limit, Response& response) {
    Problem(Status::BadRequest, "The body is not the content " + identifier.to_string()
                                    + ", as it is, nor once decompressed to at most "
                                    + to_string(limit) + " bytes.")
        .answer(response);
}

// The body stored, as it was put; or 503, telling the node's other parts
// that the content was needed, so that they look for it; or 404, while a
// search for it is held off. Either way, tells them it was asked for.
void answer_get(const IDataSource& store, const IsHeldOff& is_held_off,
                Milliseconds retry_after, const Identifier& identifier, Response& response) {
    const auto body = store.get(identifier);

    Channel::post(DataRequested::name, DataRequested{identifier});

    if (!body && is_held_off && is_held_off(identifier)) {
        Problem(Status::NotFound, "The content is not stored here, and was not found lately.")
            .answer(response);
        return;
    }

    if (!body) {
        Channel::post(DataNotFound::name, DataNotFound{identifier});
        answer_unavailable(retry_after, response);
        return;
    }

    response.set_status_code(Status::Ok);
    response.headers().set("Content-Type", string(octet_stream_content_type));
    response.set_body(Converter::text_of(*body));
}

// Stores the body as it came, once it is checked to be no larger than
// body_bytes and to be the content, and tells the node's other parts where
// it came from; or says why not.
void answer_put(const IDataSource& store, size_t content_bytes, size_t body_bytes,
                const Identifier& identifier, const Request& request, Response& response,
                const ConnectionInfo& connection) {
    if (request.body().size() > body_bytes) {
        answer_too_large(request.body().size(), body_bytes, response);
        return;
    }

    const auto body = Converter::bytes_of(request.body());

    if (!identifier.content_of(body, content_bytes)) {
        answer_not_content(identifier, content_bytes, response);
        return;
    }

    if (store.has(identifier)) {
        response.set_status_code(Status::Ok);
        return;
    }

    store.put(identifier, body);
    Channel::post(DataStored::name, DataStored{identifier, connection.node(), body.size()});
    response.set_status_code(Status::Created);
}

} // namespace

namespace api {

DataHandler::DataHandler(IDataSource::Ptr store, size_t content_bytes, Milliseconds retry_after,
                         IsHeldOff is_held_off, size_t body_bytes)
    : m_store(std::move(store))
    , m_content_bytes(content_bytes)
    , m_retry_after(checked(retry_after))
    , m_is_held_off(std::move(is_held_off))
    , m_body_bytes(checked_body_bytes(body_bytes)) {}

void DataHandler::operator()(const Request& request, Response& response,
                             const ConnectionInfo& connection) const {
    auto path = data_path(request.uri());

    if (!path) {
        Problem(Status::NotFound, "No resource exists at this path.").answer(response);
        return;
    }

    const auto method = request.method();

    if ("GET" != method && "PUT" != method) {
        Problem::answer_method_not_allowed(request, allowed_methods, response);
        return;
    }

    const auto identifier = identifier_of(std::move(*path), request, response, connection);

    if (!identifier) {
        return;
    }

    if ("GET" == method) {
        answer_get(*m_store, m_is_held_off, m_retry_after, *identifier, response);
    } else {
        answer_put(*m_store, m_content_bytes, m_body_bytes, *identifier, request, response,
                   connection);
    }
}

} // namespace api
