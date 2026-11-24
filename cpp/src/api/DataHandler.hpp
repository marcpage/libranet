#pragma once

#include <chrono>
#include <cstddef>
#include <functional>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"
#include "web/ConnectionInfo.hpp"

namespace api {

// Answers GET and PUT of /data/{algorithm}/{hash}, the content the node stores
// (HttpApi §5, §7). A GET of content not held is 503 while the node looks for
// it (§5.2), or 404 while that search is held off (§5.3).
class DataHandler {
public:
    using Seconds = std::chrono::seconds;
    // As peer::Fetch::is_held_off() says. Called from the threads serving
    // connections, several at once.
    using IsHeldOff = std::function<bool(const storage::Identifier& identifier)>;

    // As sent (HttpApi §7.1).
    static constexpr std::size_t max_body_bytes = 1 * types::Megabyte;
    // As the Python node's is.
    static constexpr Seconds default_retry_after = std::chrono::seconds(5);

    // With no is_held_off, no search is held off. Throws
    // std::invalid_argument, naming the setting, for a retry after less than
    // zero.
    explicit DataHandler(storage::IDataSource::Ptr store,
                         std::size_t content_bytes = types::max_decompressed_content_size,
                         Seconds retry_after = default_retry_after,
                         IsHeldOff is_held_off = nullptr);

    // Called from the threads serving connections, several at once. Throws
    // what the store throws, and std::bad_alloc if zlib runs out of memory.
    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& connection) const;

    ~DataHandler() = default;
    DataHandler(DataHandler&&) = default;
    DataHandler& operator=(DataHandler&&) = default;

    DataHandler() = delete;
    DataHandler(const DataHandler&) = delete;
    DataHandler& operator=(const DataHandler&) = delete;

private:
    storage::IDataSource::Ptr m_store;
    std::size_t m_content_bytes;
    Seconds m_retry_after;
    IsHeldOff m_is_held_off;

    void _get(const storage::Identifier& identifier, http::Response& response) const;
    void _put(const storage::Identifier& identifier, const http::Request& request,
              http::Response& response, const web::ConnectionInfo& connection) const;
};

} // namespace api
