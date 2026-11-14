// libFuzzer calls this with inputs it makes up, as a response read from a
// stream. Reading turns down a response it cannot read by failing the
// stream, or, for a header line, with std::runtime_error or
// std::invalid_argument; anything else it does with an input, such as
// crash, hang, or throw something else, is a fault. A response it reads is
// written back out.
#include <algorithm>
#include <cstddef>
#include <cstdint>
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
        if (stream >> response) {
            static_cast<void>(response.to_string());
        }
    } catch (const std::runtime_error&) {
        return 0;
    } catch (const std::invalid_argument&) {
        return 0;
    }

    return 0;
}
