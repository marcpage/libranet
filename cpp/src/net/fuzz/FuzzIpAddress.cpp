// libFuzzer calls this with inputs it makes up. IpAddress::parse() turns down
// text that names no IP address with an empty optional; anything else it does
// with an input, such as crash, hang, or throw, is a fault. What it reads, it
// must write as text it reads back as the same address.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>

#include "net/IpAddress.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());

    const auto address = net::IpAddress::parse(input);

    if (!address) {
        return 0;
    }

    const auto text = address->to_string();
    const auto reread = net::IpAddress::parse(text);

    if (!reread || reread->to_string() != text || reread->url_host() != address->url_host()
        || reread->is_loopback() != address->is_loopback()) {
        std::cerr << "IpAddress did not read back what it wrote: " << text << '\n';
        std::abort();
    }

    return 0;
}
