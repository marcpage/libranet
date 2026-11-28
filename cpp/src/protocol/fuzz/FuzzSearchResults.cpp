// libFuzzer calls this with inputs it makes up. SearchResults::from_body()
// turns down a body that is not a search answer, as JSON or as zlib-compressed
// JSON, with std::invalid_argument; anything else it does with an input, such
// as crash, hang, or throw something else, is a fault. What it reads, it must
// write as a body that it reads back as the same results, and writes again the
// same. Only an answer too large to send names fewer of them.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>

#include "protocol/SearchResults.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"

using protocol::SearchResults;
using storage::Identifier;

namespace {

// Far more than what the fuzzer's inputs, of a few KiB, hold, but little
// enough that a body that expands far does not take its memory.
constexpr std::size_t decompressed_limit = 1 * types::Megabyte;

// The answer body is, or nothing when from_body() turns it down.
std::optional<SearchResults> parsed(const std::string& body) {
    try {
        return SearchResults::from_body(body, decompressed_limit);
    } catch (const std::invalid_argument&) {
        return std::nullopt;
    }
}

// Whether reread names read's results, or, when its body is compressed, as
// many of the first of them as fit.
bool names_results_of(const SearchResults& reread, const SearchResults& read,
                      bool compressed) {
    const auto& named = reread.results();
    const auto& found = read.results();
    const auto same = [](const Identifier& left, const Identifier& right) {
        return left.to_string() == right.to_string();
    };

    if (named.size() > found.size() || (!compressed && named.size() != found.size())) {
        return false;
    }

    return std::equal(named.begin(), named.end(), found.begin(), same);
}

} // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());
    const auto read = parsed(input);

    if (!read) {
        return 0;
    }

    const auto body = read->body();
    const auto reread = SearchResults::from_body(body.text, decompressed_limit);

    if (!names_results_of(reread, *read, body.compressed) || reread.body().text != body.text) {
        std::cerr << "SearchResults did not read back what it wrote: " << body.text << '\n';
        std::abort();
    }

    return 0;
}
