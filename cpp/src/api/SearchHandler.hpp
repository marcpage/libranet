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

// Answers GET and POST of /data/search/{prefix}, searches for content by a
// prefix of its hash (HttpApi §6), and any other method there with 405.
//
// A GET is answered at once with what answers gives for the prefix, best
// match first, as many as fit (HttpApi §6.1), and posts
// stats::Recorder::SearchRequested, for the node to search its peers too.
// It names the node the connection carries the signature of, if any, and
// how many times more that node said the search may be passed on, in
// X-Search-Relays.
//
// A POST names what a peer holds that matches a search the node seeks, in
// the form of a search's answer (HttpApi §10.7.2). It is answered 202, and
// what it names is posted as storage::SearchAnswered. One from a connection
// that carries no node's verified signature is 401, as what it names would
// be from no node, and one whose body is not a search's answer is 400.
//
// A prefix that is not hex digits, or has more digits than the longest hash
// has, is 400. A path beneath /data/search of another form is 404.
class SearchHandler {
public:
    // How large what a POST names may be once decompressed, by default: as
    // storage.max_decompressed_list_bytes is in the Python node. The
    // protocol sets no limit; this is only a safeguard (HttpApi §21).
    static constexpr std::size_t default_decompressed_bytes = 4 * types::Megabyte;

    // Answers searches from answers. A POST whose body decompresses to more
    // than decompressed_bytes is refused, as one that is no answer is.
    // Throws std::invalid_argument for no answers.
    explicit SearchHandler(std::shared_ptr<storage::SearchAnswers> answers,
                           std::size_t decompressed_bytes = default_decompressed_bytes);
    ~SearchHandler() = default;
    SearchHandler(SearchHandler&&) = default;
    SearchHandler& operator=(SearchHandler&&) = default;

    SearchHandler() = delete;
    SearchHandler(const SearchHandler&) = delete;
    SearchHandler& operator=(const SearchHandler&) = delete;

    // Called from the threads serving connections, several at once. Throws
    // what the store throws as answers looks through it, and std::bad_alloc
    // if zlib runs out of memory, which the server answers with 500.
    void operator()(const http::Request& request, http::Response& response,
                    const web::ConnectionInfo& connection) const;

private:
    std::shared_ptr<storage::SearchAnswers> m_answers;
    std::size_t m_decompressed_bytes;

    void _get(const storage::HashPrefix& prefix, const http::Request& request,
              http::Response& response, const web::ConnectionInfo& connection) const;
    void _post(const storage::HashPrefix& prefix, const http::Request& request,
               http::Response& response, const web::ConnectionInfo& connection) const;
};

} // namespace api
