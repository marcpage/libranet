#pragma once

#include "../http/Request.hpp"
#include "../http/Response.hpp"
#include "IDataSource.hpp"

namespace storage {

class HttpHandler {
public:
    explicit HttpHandler(IDataSource::Ptr source);
    ~HttpHandler() = default;

    void operator()(const http::Request& request, http::Response& response);

    HttpHandler(HttpHandler&&) = default;
    HttpHandler& operator=(HttpHandler&&) = default;

    HttpHandler() = delete;
    HttpHandler(const HttpHandler&) = delete;
    HttpHandler& operator=(const HttpHandler&) = delete;

private:
    IDataSource::Ptr m_source;
};

} // namespace storage
