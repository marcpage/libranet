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

// Answers GET and PUT of /data/{algorithm}/{hash}, the content the node
// stores (HttpApi §5, §7), and any other method there with 405. A GET is
// the body stored, as it was put, compressed or not. One for content not
// held, which the node will look for, is 503, with a Retry-After of
// network.retry_after_seconds, and posts storage::DataNotFound (HttpApi
// §5.2); while the search for it is held off, as one that found nothing
// ended lately, it is 404, and the node does not look (HttpApi §5.3). Each
// GET, held or not, posts storage::DataRequested. A PUT is checked against
// its path, as it is or once decompressed, stored as it came, and answered
// 201, or 200 for content already held, which is not stored again. Content
// newly stored is posted as storage::DataStored, naming the node the
// connection carries the signature of. An identifier that is not valid, as
// one of an algorithm the node does not know, is 400. A path beneath /data
// of another form, or beneath one of its other endpoints, is 404: it names
// no content.
class DataHandler {
public:
    using Seconds = std::chrono::seconds;
    // Whether a new search for the content identifier names would be held
    // off now, as peer::Fetch::is_held_off() says. Called from the threads
    // serving connections, several at once.
    using IsHeldOff = std::function<bool(const storage::Identifier& identifier)>;

    // The largest body a PUT may have, as sent (HttpApi §7.1).
    static constexpr std::size_t max_body_bytes = 1 * types::Megabyte;
    // How large content sent compressed may be once decompressed, by
    // default: as large as the Python node reads a bundle. The protocol sets
    // no limit; this is only a safeguard (HttpApi §21).
    static constexpr std::size_t default_content_bytes = 16 * types::Megabyte;
    // What network.retry_after_seconds is when not set, as the Python
    // node's is.
    static constexpr Seconds default_retry_after = std::chrono::seconds(5);

    // Serves what store holds, and stores to it. A PUT whose body
    // decompresses to more than content_bytes is refused, as one that does
    // not hash to its path is. A GET of content not held asks the client to
    // retry after retry_after, unless is_held_off says the search for it is
    // held off; with no function, none is. Throws std::invalid_argument,
    // naming the setting, for a retry after less than zero.
    explicit DataHandler(storage::IDataSource::Ptr store,
                         std::size_t content_bytes = default_content_bytes,
                         Seconds retry_after = default_retry_after,
                         IsHeldOff is_held_off = nullptr);
    ~DataHandler() = default;
    DataHandler(DataHandler&&) = default;
    DataHandler& operator=(DataHandler&&) = default;

    DataHandler() = delete;
    DataHandler(const DataHandler&) = delete;
    DataHandler& operator=(const DataHandler&) = delete;

    // Called from the threads serving connections, several at once. Throws
    // what the store throws, and std::bad_alloc if zlib runs out of memory,
    // which the server answers with 500.
    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& connection) const;

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
