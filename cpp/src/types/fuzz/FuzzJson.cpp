// libFuzzer calls this with inputs it makes up. Json::parse turns down an
// input it cannot parse with std::invalid_argument; anything else it does
// with an input, such as crash, hang, or throw something else, is a fault.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>

#include "types/Json.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());

    try {
        const types::Json value = types::Json::parse(input);
        static_cast<void>(value.dump());
        static_cast<void>(value.dump(2));
    } catch (const std::invalid_argument&) {
        return 0;
    }

    return 0;
}
