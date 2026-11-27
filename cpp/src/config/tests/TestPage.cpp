#include <gtest/gtest.h>
#include <string>
#include <string_view>
#include <utility>

#include "config/Page.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "io/FileReader.hpp"
#include "net/Address.hpp"
#include "web/ConnectionInfo.hpp"

using config::Page;
using http::Request;
using http::Response;
using io::FileReader;
using net::Address;
using web::ConnectionInfo;

namespace {

// A connection from this machine, to /config's port: the loopback address,
// a port the system might pick for a client, and the port /config might be
// on.
constexpr std::string_view loopback = "127.0.0.1";
constexpr Address::Port remote_port = 50'000;
constexpr Address::Port local_port = 8'180;

// A request of method for uri, with no body.
Request request_of(std::string method, std::string uri) {
    Request request;

    request.set_method(std::move(method));
    request.set_uri(std::move(uri));
    return request;
}

// What page answers request with.
Response answer(const Page& page, const Request& request) {
    const ConnectionInfo connection(Address(loopback, remote_port), Address(loopback, local_port));
    Response response;

    page(request, response, connection);
    return response;
}

// applications/config/index.html, as the source tree has it.
std::string page_source() {
    return FileReader(LIBRANET_CONFIG_PAGE).read_all();
}

} // namespace

TEST(PageTest, IsTheConfigApplicationsPage) {
    const auto response = answer(Page(), request_of("GET", "/config/"));

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.body(), page_source());
}

TEST(PageTest, IsSentAsHtml) {
    const auto response = answer(Page(), request_of("GET", "/config/"));

    EXPECT_EQ(response.headers().get("Content-Type"), "text/html; charset=utf-8");
}

TEST(PageTest, IsTheSameWhateverThePath) {
    const Page page;
    const auto source = page_source();

    for (const auto* const uri : {"/config", "/config/", "/config/index.html", "/config/x?y=1"}) {
        const auto response = answer(page, request_of("GET", uri));

        EXPECT_EQ(response.status_code(), 200) << uri;
        EXPECT_EQ(response.body(), source) << uri;
    }
}

TEST(PageTest, RefusesAnyOtherMethod) {
    const Page page;

    for (const auto* const method : {"HEAD", "POST", "PUT", "DELETE"}) {
        const auto response = answer(page, request_of(method, "/config/"));

        EXPECT_EQ(response.status_code(), 405) << method;
        EXPECT_EQ(response.headers().get("Allow"), "GET") << method;
        EXPECT_EQ(response.headers().get("Content-Type"), "application/problem+json") << method;
    }
}
