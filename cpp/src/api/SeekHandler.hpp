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

// Answers GET and POST of /data/seek, the seek list (HttpApi §10.7), and any
// other method there with 405. A GET is the list last set, as it is sent;
// until one is, an empty one. A POST is a peer's own list, answered 202 and
// logged, as nothing here acts on it yet, or 400, with the problem type
// invalid-list, when it is not a seek list (§10.5). A path beneath
// /data/seek is 404: it names nothing.
class SeekHandler {
public:
    // A POST whose list decompresses to more than list_bytes is refused, as
    // one that is not a seek list is.
    explicit SeekHandler(std::size_t list_bytes = types::max_decompressed_list_size);
    ~SeekHandler() = default;

    SeekHandler(const SeekHandler&) = delete;
    SeekHandler& operator=(const SeekHandler&) = delete;
    SeekHandler(SeekHandler&&) = delete;
    SeekHandler& operator=(SeekHandler&&) = delete;

    // Serves new_list from now on, as the node last derived it. Throws what
    // protocol::SeekList::write() throws.
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
