// libFuzzer calls this with inputs it makes up. Address::parse turns down
// an input that is not a host and port with std::invalid_argument;
// anything else it does with an input, such as crash, hang, or throw
// something else, is a fault. What it parses, it must write as text it reads
// back as the same host and port. It is never resolved: that would use the
// network.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>

#include "net/Address.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());
    std::optional<net::Address> address;

    try {
        address = net::Address::parse(input);
    } catch (const std::invalid_argument&) {
        return 0;
    }

    const std::string text = address->to_string();
    const net::Address reread = net::Address::parse(text);

    if (reread.host() != address->host() || reread.port() != address->port()
        || reread.to_string() != text) {
        std::cerr << "Address did not read back what it wrote: " << text << '\n';
        std::abort();
    }

    return 0;
}
