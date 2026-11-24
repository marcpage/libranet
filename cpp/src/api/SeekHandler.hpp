#pragma once

#include <cstddef>
#include <mutex>
#include <string>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "protocol/SeekList.hpp"
#include "types/Constants.hpp"
#include "web/ConnectionInfo.hpp"

namespace api {

// Answers GET and POST of /data/seek, the seek list (HttpApi §10.7). A POST is
// logged, as nothing acts on it yet.
class SeekHandler {
public:
    // Once decompressed, as the Python node's
    // storage.max_decompressed_list_bytes; only a safeguard (HttpApi §10.7.1).
    static constexpr std::size_t default_list_bytes = 4 * types::Megabyte;

    explicit SeekHandler(std::size_t list_bytes = default_list_bytes);
    ~SeekHandler() = default;

    SeekHandler(const SeekHandler&) = delete;
    SeekHandler& operator=(const SeekHandler&) = delete;
    SeekHandler(SeekHandler&&) = delete;
    SeekHandler& operator=(SeekHandler&&) = delete;

    // Throws what protocol::SeekList::write() throws.
    void set_list(const protocol::SeekList& new_list);

    // Called from the threads serving connections, several at once, while
    // set_list() may be called from another.
    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& connection) const;

private:
    std::size_t m_list_bytes;
    mutable std::mutex m_mutex;
    std::string m_body; // Guarded by m_mutex

    void _get(http::Response& response) const;
    void _post(const http::Request& request, http::Response& response,
               const web::ConnectionInfo& connection) const;
};

} // namespace api
