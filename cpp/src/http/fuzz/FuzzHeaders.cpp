// libFuzzer calls this with inputs it makes up. Reading headers turns down
// a line it cannot read with std::runtime_error or std::invalid_argument;
// anything else it does with an input, such as crash, hang, or throw
// something else, is a fault. Headers it reads are written back out as text
// it reads back as the same headers.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>

#include "http/Headers.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());
    std::istringstream stream(input);
    http::Headers headers;

    try {
        stream >> headers;
    } catch (const std::runtime_error&) {
        return 0;
    } catch (const std::invalid_argument&) {
        return 0;
    }

    const std::string text = headers.to_string();
    std::istringstream text_stream(text);
    http::Headers reread;
    text_stream >> reread;

    if (reread != headers || reread.to_string() != text) {
        std::cerr << "Headers did not read back what they wrote: " << text << '\n';
        std::abort();
    }

    return 0;
}
