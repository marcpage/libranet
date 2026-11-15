// libFuzzer calls this with inputs it makes up. Query::parse takes any
// text, so anything it does with an input other than return, such as crash,
// hang, or throw, is a fault. The query is written back out as text it reads
// back as the same query.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>

#include "http/Query.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());

    // Read back as a URL carries it, after its '?', which Query::parse
    // drops: a query that itself starts with '?' keeps it that way.
    const std::string text = http::Query::parse(input).to_string();
    const std::string rewritten = http::Query::parse("?" + text).to_string();

    if (rewritten != text) {
        std::cerr << "Query did not read back what it wrote: " << text << '\n';
        std::abort();
    }

    return 0;
}
