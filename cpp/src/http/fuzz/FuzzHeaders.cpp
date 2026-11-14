// libFuzzer calls this with inputs it makes up. Reading headers turns down
// a line it cannot read with std::runtime_error or std::invalid_argument;
// anything else it does with an input, such as crash, hang, or throw
// something else, is a fault. Headers it reads are written back out.
#include <algorithm>
#include <cstddef>
#include <cstdint>
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
        static_cast<void>(headers.to_string());
    } catch (const std::runtime_error&) {
        return 0;
    } catch (const std::invalid_argument&) {
        return 0;
    }

    return 0;
}
