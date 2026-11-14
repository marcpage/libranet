// libFuzzer calls this with inputs it makes up. Integer turns down text
// that is not a number with std::invalid_argument; anything else it does
// with an input, such as crash, hang, or throw something else, is a fault.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>

#include "types/Integer.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());

    try {
        const types::Integer value(input);
        static_cast<void>(value.to_string());
    } catch (const std::invalid_argument&) {
        return 0;
    }

    return 0;
}
