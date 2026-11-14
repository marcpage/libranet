// libFuzzer calls this with inputs it makes up. Address::parse turns down
// an input that is not a host and port with std::invalid_argument;
// anything else it does with an input, such as crash, hang, or throw
// something else, is a fault. It is never resolved: that would use the
// network.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>

#include "net/Address.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());

    try {
        const net::Address address = net::Address::parse(input);
        static_cast<void>(address.to_string());
    } catch (const std::invalid_argument&) {
        return 0;
    }

    return 0;
}
