#include "SearchHandler.hpp"

#include <charconv>
#include <cstddef>
#include <iterator>
#include <limits>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string_view>
#include <system_error>
#include <utility>

#include "http/Problem.hpp"
#include "http/Request.hpp"
#include "http/Response.hpp"
#include "http/ResponseCode.hpp"
#include "logger/Logger.hpp"
#include "msg/Channel.hpp"
#include "protocol/SearchResults.hpp"
#include "stats/Recorder.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/SearchAnswered.hpp"
#include "storage/SearchAnswers.hpp"
#include "types/Constants.hpp"
#include "web/ConnectionInfo.hpp"

using http::Problem;
using http::Request;
using http::Response;
using msg::Channel;
using protocol::SearchResults;
using std::errc;
using std::from_chars;
using std::invalid_argument;
using std::nullopt;
using std::numeric_limits;
using std::optional;
using std::ptrdiff_t;
using std::shared_ptr;
using std::size_t;
using std::string_view;
using storage::HashPrefix;
using storage::SearchAnswered;
using storage::SearchAnswers;
using types::search_path_prefix;
using types::search_relays_header;
using web::ConnectionInfo;

namespace {

using Status = http::ResponseCode::Status;
using SearchRequested = stats::Recorder::SearchRequested;

// The methods /data/search/{prefix} takes, as Allow names them.
constexpr string_view allowed_methods = "GET, POST";

// The prefix request names, not yet checked, when its path is
// /data/search/{prefix}, the prefix not empty; nothing otherwise.
optional<string_view> search_path(const Request& request) {
    const auto path = request.path();

    if (path.substr(0, search_path_prefix.size()) != search_path_prefix) {
        return nullopt;
    }

    const auto prefix = path.substr(search_path_prefix.size());

    if (prefix.empty() || string_view::npos != prefix.find('/')) {
        return nullopt;
    }

    return prefix;
}

// The prefix text, from the path of request on connection, names, or
// nothing when it names none, having answered 400 saying why.
optional<HashPrefix> prefix_of(string_view text, const Request& request, Response& response,
                               const ConnectionInfo& connection) {
    try {
        return HashPrefix::parse(text);
    } catch (const invalid_argument& error) {
        lDebug << "Refused" << request.method() << request.uri() << "from"
               << connection.remote_address().to_string() + ":" << error.what();
        Problem(Problem::Type::InvalidSearchPrefix, Status::BadRequest, error.what())
            .answer(response);
        return nullopt;
    }
}

// How many times more request says the search it asks may be passed on, as
// a whole number: none when it says nothing usable, and as many as can be
// counted when it says more, which the node then holds to what it allows
// (HttpApi §6).
size_t relays_of(const Request& request) {
    const auto said = request.headers().get(search_relays_header);

    if (!said) {
        return 0;
    }

    const auto* const first = said->data();
    const auto* const last = std::next(first, static_cast<ptrdiff_t>(said->size()));
    size_t relays = 0;
    const auto [stop, error] = from_chars(first, last, relays);

    if (stop != last) {
        return 0;
    }

    if (errc::result_out_of_range == error) {
        return numeric_limits<size_t>::max();
    }

    return errc() == error ? relays : 0;
}

// What the body of request, on connection, names, read as a search's answer
// that decompresses to no more than limit bytes, or nothing when it is none,
// having answered 400 saying why (HttpApi §10.7.2).
optional<SearchResults> named_in(const Request& request, size_t limit, Response& response,
                                 const ConnectionInfo& connection) {
    try {
        return SearchResults::from_body(request.body(), limit);
    } catch (const invalid_argument& error) {
        lDebug << "Refused the search answer from"
               << connection.remote_address().to_string() + ":" << error.what();
        Problem(Problem::Type::InvalidList, Status::BadRequest, error.what()).answer(response);
        return nullopt;
    }
}

shared_ptr<SearchAnswers> checked(shared_ptr<SearchAnswers> answers) {
    if (!answers) {
        throw invalid_argument("A SearchHandler needs the answers to give");
    }

    return answers;
}

// What is known now that matches prefix, best first, and tells the node's
// other parts of the search, so that they search its peers too, as far as
// the node that asked allows.
void answer_get(SearchAnswers& answers, const HashPrefix& prefix, const Request& request,
                Response& response, const ConnectionInfo& connection) {
    const auto& node = connection.node();

    response.set_status_code(Status::Ok);
    SearchResults(answers.answer(prefix)).answer(response);
    Channel::post(SearchRequested::name,
                  SearchRequested{prefix, node, node ? relays_of(request) : 0});
}

// Tells the node's other parts what the node whose signature the
// connection carries named for prefix, decompressed to no more than
// decompressed_bytes; or says why not.
void answer_post(size_t decompressed_bytes, const HashPrefix& prefix, const Request& request,
                 Response& response, const ConnectionInfo& connection) {
    const auto& node = connection.node();

    if (!node) {
        Problem(Problem::Type::SignatureRequired, Status::Unauthorized,
                "This request must carry the sending node's verified signature.")
            .answer(response);
        return;
    }

    const auto named = named_in(request, decompressed_bytes, response, connection);

    if (!named) {
        return;
    }

    if (!named->results().empty()) {
        Channel::post(SearchAnswered::name, SearchAnswered{prefix, *node, named->results()});
    }

    response.set_status_code(Status::Accepted);
}

} // namespace

namespace api {

SearchHandler::SearchHandler(shared_ptr<SearchAnswers> answers, size_t decompressed_bytes)
    : m_answers(checked(std::move(answers)))
    , m_decompressed_bytes(decompressed_bytes) {}

void SearchHandler::operator()(const Request& request, Response& response,
                               const ConnectionInfo& connection) const {
    const auto path = search_path(request);

    if (!path) {
        Problem::answer_not_found(response);
        return;
    }

    const auto method = request.method();

    if ("GET" != method && "POST" != method) {
        Problem::answer_method_not_allowed(request, allowed_methods, response);
        return;
    }

    const auto prefix = prefix_of(*path, request, response, connection);

    if (!prefix) {
        return;
    }

    if ("GET" == method) {
        answer_get(*m_answers, *prefix, request, response, connection);
    } else {
        answer_post(m_decompressed_bytes, *prefix, request, response, connection);
    }
}

} // namespace api
