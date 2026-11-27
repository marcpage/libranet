#pragma once

#include <string>
#include <string_view>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "web/ConnectionInfo.hpp"

namespace config {

// The /config page (HTTP API §2.3), built into the node: the build makes it
// from applications/config/index.html. A GET is answered with the page, and
// any other method with 405, whatever the path. Safe to use from the threads
// serving connections, several at once.
class Page {
public:
    static constexpr std::string_view content_type = "text/html; charset=utf-8";

    Page();

    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& connection) const;

private:
    std::string m_html; // The page, as it is sent

    // Defined in the source file the build makes.
    [[nodiscard]] static std::string _built_html();
};

} // namespace config
