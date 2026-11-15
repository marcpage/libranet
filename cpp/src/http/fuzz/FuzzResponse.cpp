// libFuzzer calls this with inputs it makes up, as a response read from a
// stream. Reading turns down a response it cannot read by failing the
// stream, or, for a header line, with std::runtime_error or
// std::invalid_argument; anything else it does with an input, such as
// crash, hang, or throw something else, is a fault. A response it reads is
// written back out as text it reads back as the same response.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>

#include "http/Response.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());
    std::istringstream stream(input);
    http::Response response;

    try {
        if (!(stream >> response)) {
            return 0;
        }
    } catch (const std::runtime_error&) {
        return 0;
    } catch (const std::invalid_argument&) {
        return 0;
    }

    const std::string text = response.to_string();
    std::istringstream text_stream(text);
    http::Response reread;

    if (!(text_stream >> reread) || reread.to_string() != text) {
        std::cerr << "Response did not read back what it wrote: " << text << '\n';
        std::abort();
    }

    return 0;
}
