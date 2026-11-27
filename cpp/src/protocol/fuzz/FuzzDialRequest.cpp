// libFuzzer calls this with inputs it makes up. DialRequest::parse() turns
// down text that is not a request for an address the node can dial with
// std::invalid_argument; anything else it does with an input, such as crash,
// hang, or throw something else, is a fault. What it reads, it must write as
// text it reads back as the same request, and its endpoint must be dialed as
// it is written.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

#include "protocol/DialRequest.hpp"

using protocol::DialRequest;

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());
    std::string text;
    std::string endpoint;

    try {
        const auto request = DialRequest::parse(input);

        text = request.to_string();
        endpoint = request.endpoint();
    } catch (const std::invalid_argument&) {
        return 0;
    }

    if (DialRequest::parse(text).to_string() != text
        || DialRequest(endpoint).endpoint() != endpoint) {
        std::cerr << "DialRequest did not read back what it wrote: " << text << '\n';
        std::abort();
    }

    return 0;
}
