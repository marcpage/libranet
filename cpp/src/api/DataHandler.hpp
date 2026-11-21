#pragma once

#include <cstddef>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"
#include "web/ConnectionInfo.hpp"

namespace api {

// Answers GET and PUT of /data/{algorithm}/{hash}, the content the node
// stores (HttpApi §5, §7), and any other method there with 405. A GET is
// the body stored, as it was put, compressed or not; one for content not
// held is 404, and posts storage::DataNotFound. A PUT is checked against its
// path, as it is or once decompressed, stored as it came, and answered 201,
// or 200 for content already held, which is not stored again. Content newly
// stored is posted as storage::DataStored, naming the node the connection
// carries the signature of. An identifier that is not valid, as one of an
// algorithm the node does not know, is 400. A path beneath /data of another
// form, or beneath one of its other endpoints, is 404: it names no content.
class DataHandler {
public:
    // The largest body a PUT may have, as sent (HttpApi §7.1).
    static constexpr std::size_t max_body_bytes = 1 * types::Megabyte;
    // How large content sent compressed may be once decompressed, by
    // default: as large as the Python node reads a bundle. The protocol sets
    // no limit; this is only a safeguard (HttpApi §21).
    static constexpr std::size_t default_content_bytes = 16 * types::Megabyte;

    // Serves what store holds, and stores to it. A PUT whose body
    // decompresses to more than content_bytes is refused, as one that does
    // not hash to its path is.
    explicit DataHandler(storage::IDataSource::Ptr store,
                         std::size_t content_bytes = default_content_bytes);
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

    void _get(const storage::Identifier& identifier, http::Response& response) const;
    void _put(const storage::Identifier& identifier, const http::Request& request,
              http::Response& response, const web::ConnectionInfo& connection) const;
};

} // namespace api
