#pragma once

#include <string>
#include <string_view>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "web/ConnectionInfo.hpp"

namespace config {

// The /config page (HTTP API §2.3), built in from
// applications/config/index.html.
class Page {
public:
    static constexpr std::string_view content_type = "text/html; charset=utf-8";

    Page();

    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& connection) const;

private:
    std::string m_html;

    // Defined in the source file the build makes.
    [[nodiscard]] static std::string _built_html();
};

} // namespace config
