#include <gtest/gtest.h>
#include <cstddef>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "storage/HttpHandler.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"

using http::Request;
using http::Response;
using std::nullopt;
using std::optional;
using std::string;
using std::vector;
using storage::HttpHandler;
using storage::IDataSource;
using storage::Identifier;

namespace {

// A source holding one piece of data, which records each identifier it is
// asked for.
class OneItemSource : public IDataSource {
public:
    explicit OneItemSource(const string& text)
        : m_data(text.size()) {
        for (std::size_t index = 0; index < text.size(); ++index) {
            m_data.at(index) = static_cast<std::byte>(text.at(index));
        }
    }

    [[nodiscard]] bool has(const Identifier& identifier) const override {
        return get(identifier).has_value();
    }

    [[nodiscard]] optional<Data> get(const Identifier& identifier) const override {
        m_asked.push_back(identifier.to_string());

        if (Identifier::from(m_data).to_string() == identifier.to_string()) {
            return m_data;
        }

        return nullopt;
    }

    [[nodiscard]] Identifier put(const Data& data) const override {
        return Identifier::from(data);
    }

    [[nodiscard]] Identifier held() const {
        return Identifier::from(m_data);
    }

    [[nodiscard]] const vector<string>& asked() const {
        return m_asked;
    }

private:
    Data m_data;
    mutable vector<string> m_asked;
};

// The response to a signed GET of the URI.
Response fetch(const HttpHandler& handler, string uri) {
    Request request;
    Response response;

    request.set_method("GET");
    request.set_uri(std::move(uri));
    request.headers().set("X-Node-Signature", "signature");
    handler(request, response);
    return response;
}

} // namespace

TEST(HttpHandlerTest, HeldDataIsReturned) {
    const auto source = std::make_shared<OneItemSource>("hello");
    const HttpHandler handler(source);

    const auto response = fetch(handler, "/data/" + source->held().to_string());

    EXPECT_EQ(response.status_code(), 200);
    EXPECT_EQ(response.body(), "hello");
}

// ".." as the algorithm would reach the directory above a store (issue
// #782): it is refused before the source is asked.
TEST(HttpHandlerTest, UnsupportedAlgorithmIsBadRequest) {
    const auto source = std::make_shared<OneItemSource>("hello");
    const HttpHandler handler(source);

    const auto response = fetch(handler, "/data/../" + source->held().hash());

    EXPECT_EQ(response.status_code(), 400);
    EXPECT_TRUE(source->asked().empty());
}

TEST(HttpHandlerTest, MissingHashIsBadRequest) {
    const auto source = std::make_shared<OneItemSource>("hello");
    const HttpHandler handler(source);

    const auto response = fetch(handler, "/data/sha256");

    EXPECT_EQ(response.status_code(), 400);
    EXPECT_TRUE(source->asked().empty());
}
