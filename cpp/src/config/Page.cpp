#include "Page.hpp"

#include <string>

#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "web/ConnectionInfo.hpp"

using http::Problem;
using http::Request;
using http::Response;
using std::string;
using web::ConnectionInfo;

namespace {

using Status = http::ResponseCode::Status;

} // namespace

namespace config {

Page::Page()
    : m_html(_built_html()) {}

void Page::operator()(const Request& request, Response& response,
                      [[maybe_unused]] const ConnectionInfo& connection) const {
    if ("GET" != request.method()) {
        Problem::answer_method_not_allowed(request, "GET", response);
        return;
    }

    response.set_status_code(Status::Ok);
    response.headers().set("Content-Type", string(content_type));
    response.set_body(m_html);
}

} // namespace config
