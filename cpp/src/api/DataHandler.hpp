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

// GET and PUT of /data/{algorithm}/{hash} (HttpApi §5, §7).
class DataHandler {
public:
    using Milliseconds = std::chrono::milliseconds;
    // As peer::Fetch::is_held_off(). Called from several threads at once.
    using IsHeldOff = std::function<bool(const storage::Identifier& identifier)>;

    // As sent (HttpApi §7.1).
    static constexpr std::size_t max_body_bytes = 1 * types::Megabyte;
    // As the Python node's is.
    static constexpr Milliseconds default_retry_after = std::chrono::seconds(5);

    // With no is_held_off, no search is held off. Retry-After carries whole
    // seconds, so a client is told retry_after rounded up to the second.
    // Throws std::invalid_argument for a negative retry_after, or body_bytes
    // of zero or more than max_body_bytes.
    explicit DataHandler(storage::IDataSource::Ptr store,
                         std::size_t content_bytes = types::max_decompressed_content_size,
                         Milliseconds retry_after = default_retry_after,
                         IsHeldOff is_held_off = nullptr, std::size_t body_bytes = max_body_bytes);

    // Throws what the store throws, and std::bad_alloc if zlib runs out of
    // memory.
    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& connection) const;

    ~DataHandler() = default;
    DataHandler(DataHandler&&) = default;
    DataHandler& operator=(DataHandler&&) = default;

    DataHandler(const DataHandler&) = delete;
    DataHandler& operator=(const DataHandler&) = delete;

private:
    storage::IDataSource::Ptr m_store;
    std::size_t m_content_bytes;
    Milliseconds m_retry_after;
    IsHeldOff m_is_held_off;
    std::size_t m_body_bytes;
};

} // namespace api
