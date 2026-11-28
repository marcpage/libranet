#include "NotOffered.hpp"

#include <string>
#include <string_view>

#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "web/ConnectionInfo.hpp"

using config::NotOffered;
using http::Problem;
using http::Request;
using http::Response;
using std::string;
using std::string_view;
using web::ConnectionInfo;

namespace {

using Status = http::ResponseCode::Status;

// Whether path is beneath parent.
bool is_beneath(string_view path, string_view parent) {
    return path.size() > parent.size() && path.substr(0, parent.size()) == parent
           && '/' == path.at(parent.size());
}

// Whether path is feature's.
bool has(const NotOffered::Feature& feature, string_view path) {
    return path == feature.path || (feature.beneath && is_beneath(path, feature.path));
}

} // namespace

namespace config {

NotOffered::NotOffered(const Feature& feature) noexcept
    : m_feature(feature) {}

void NotOffered::operator()(const Request& request, Response& response,
                            const ConnectionInfo& /*connection*/) const {
    if (!has(m_feature, request.path())) {
        Problem::answer_not_found(response);
        return;
    }

    Problem(Status::NotFound, "This node does not offer " + string(m_feature.name) + " yet.")
        .answer(response);
}

} // namespace config
