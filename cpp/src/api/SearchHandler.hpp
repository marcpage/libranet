#pragma once

#include <cstddef>
#include <memory>

#include "http/Request.hpp"
#include "http/Response.hpp"
#include "storage/SearchAnswers.hpp"
#include "types/Constants.hpp"
#include "web/ConnectionInfo.hpp"

namespace api {

// GET and POST of /data/search/{prefix} (HttpApi §6, §10.7.2).
class SearchHandler {
public:
    // Throws std::invalid_argument for no answers.
    explicit SearchHandler(std::shared_ptr<storage::SearchAnswers> answers,
                           std::size_t decompressed_bytes = types::max_decompressed_list_size);

    // Throws what the store throws, and std::bad_alloc if zlib runs out of
    // memory.
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
};

} // namespace api
