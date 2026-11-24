#pragma once

#include <cstddef>
#include <memory>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/SearchAnswers.hpp"
#include "types/Constants.hpp"
#include "web/ConnectionInfo.hpp"

namespace api {

// Answers GET and POST of /data/search/{prefix} (HttpApi §6): a GET is a
// search, answered at once from answers, and a POST names what a peer holds
// that matches a search the node seeks (§10.7.2).
class SearchHandler {
public:
    // HttpApi §6.
    static constexpr auto search_relays_header = "X-Search-Relays";
    // Once decompressed, as the Python node's
    // storage.max_decompressed_list_bytes; only a safeguard (HttpApi §21).
    static constexpr std::size_t default_decompressed_bytes = 4 * types::Megabyte;

    // Throws std::invalid_argument for no answers.
    explicit SearchHandler(std::shared_ptr<storage::SearchAnswers> answers,
                           std::size_t decompressed_bytes = default_decompressed_bytes);

    // Called from the threads serving connections, several at once. Throws
    // what the store throws, and std::bad_alloc if zlib runs out of memory.
    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& connection) const;

    ~SearchHandler() = default;
    SearchHandler(SearchHandler&&) = default;
    SearchHandler& operator=(SearchHandler&&) = default;

    SearchHandler() = delete;
    SearchHandler(const SearchHandler&) = delete;
    SearchHandler& operator=(const SearchHandler&) = delete;

private:
    std::shared_ptr<storage::SearchAnswers> m_answers;
    std::size_t m_decompressed_bytes;

    void _get(const storage::HashPrefix& prefix, const http::Request& request,
              http::Response& response, const web::ConnectionInfo& connection) const;
    void _post(const storage::HashPrefix& prefix, const http::Request& request,
               http::Response& response, const web::ConnectionInfo& connection) const;
};

} // namespace api
