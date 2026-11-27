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

// GET and POST of /data/seek (HttpApi §10.7). A POST is only logged, for now.
class SeekHandler {
public:
    // list_bytes is the most a list posted may decompress to, and the list
    // served is sent in fewer than served_bytes.
    explicit SeekHandler(std::size_t list_bytes = types::max_decompressed_list_size,
                         std::size_t served_bytes = protocol::SeekList::max_size);

    // Any thread may call it. Throws what protocol::SeekList::body() throws.
    void set_list(const protocol::SeekList& new_list);

    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& connection) const;

    ~SeekHandler() = default;

    SeekHandler(const SeekHandler&) = delete;
    SeekHandler& operator=(const SeekHandler&) = delete;
    SeekHandler(SeekHandler&&) = delete;
    SeekHandler& operator=(SeekHandler&&) = delete;

private:
    std::size_t m_list_bytes;
    std::size_t m_served_bytes;
    mutable std::mutex m_mutex;
    std::string m_body; // Guarded by m_mutex
};

} // namespace api
