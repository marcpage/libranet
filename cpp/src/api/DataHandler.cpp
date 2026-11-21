#include "DataHandler.hpp"

#include <algorithm>
#include <array>
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
#include "msg/Channel.hpp"
#include "storage/DataNotFound.hpp"
#include "storage/DataStored.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
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
using storage::DataStored;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;
using web::ConnectionInfo;
using namespace std::string_view_literals;

namespace {

using Status = ResponseCode::Status;

// The algorithm and hash a path names, not yet checked.
struct DataPath {
    string algorithm;
    string hash;
};

constexpr string_view data_prefix = "/data/";

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

    if (path.substr(0, data_prefix.size()) != data_prefix) {
        return nullopt;
    }

    const auto rest = path.substr(data_prefix.size());
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

// The identifier path names, or nothing when it names none that is valid,
// having answered 400 saying why (HttpApi §5.4).
optional<Identifier> identifier_of(DataPath path, Response& response) {
    try {
        return Identifier(std::move(path.algorithm), std::move(path.hash));
    } catch (const invalid_argument& error) {
        Problem(Problem::Type::InvalidContentAddress, Status::BadRequest, error.what())
            .answer(response);
        return nullopt;
    }
}

// Answers 413 for a body of size bytes, larger than a PUT may have.
void answer_too_large(size_t size, Response& response) {
    Problem(Problem::Type::ContentTooLarge, Status::PayloadTooLarge,
            "Request bodies are limited to " + to_string(api::DataHandler::max_body_bytes)
                + " bytes; this one has " + to_string(size) + ".")
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

} // namespace

namespace api {

DataHandler::DataHandler(IDataSource::Ptr store, size_t content_bytes)
    : m_store(std::move(store))
    , m_content_bytes(content_bytes) {}

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

    const auto identifier = identifier_of(std::move(*path), response);

    if (!identifier) {
        return;
    }

    if ("GET" == method) {
        _get(*identifier, response);
    } else {
        _put(*identifier, request, response, connection);
    }
}

// The body stored, as it was put; or 404, telling the node's other parts
// that the content was needed.
void DataHandler::_get(const Identifier& identifier, Response& response) const {
    const auto body = m_store->get(identifier);

    if (!body) {
        Channel::post(DataNotFound::name, DataNotFound{identifier});
        Problem(Status::NotFound, "The content is not stored here.").answer(response);
        return;
    }

    response.set_status_code(Status::Ok);
    response.headers().set("Content-Type", "application/octet-stream");
    response.set_body(Converter::text_of(*body));
}

// Stores the body as it came, once it is checked to be the content, and
// tells the node's other parts where it came from; or says why not.
void DataHandler::_put(const Identifier& identifier, const Request& request, Response& response,
                       const ConnectionInfo& connection) const {
    if (request.body().size() > max_body_bytes) {
        answer_too_large(request.body().size(), response);
        return;
    }

    const auto body = Converter::bytes_of(request.body());

    if (!identifier.content_of(body, m_content_bytes)) {
        answer_not_content(identifier, m_content_bytes, response);
        return;
    }

    if (m_store->has(identifier)) {
        response.set_status_code(Status::Ok);
        return;
    }

    m_store->put(identifier, body);
    Channel::post(DataStored::name, DataStored{identifier, connection.node(), body.size()});
    response.set_status_code(Status::Created);
}

} // namespace api
