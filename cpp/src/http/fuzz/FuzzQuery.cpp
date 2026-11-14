// libFuzzer calls this with inputs it makes up. Query::parse takes any
// text, so anything it does with an input other than return, such as crash,
// hang, or throw, is a fault. The query is written back out as well.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <string>

#include "http/Query.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());

    const http::Query query = http::Query::parse(input);
    static_cast<void>(query.to_string());

    return 0;
}
