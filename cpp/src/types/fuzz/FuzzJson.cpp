// libFuzzer calls this with inputs it makes up. Json::parse turns down an
// input it cannot parse with std::invalid_argument; anything else it does
// with an input, such as crash, hang, or throw something else, is a fault.
// What it parses, it must write in a form it reads back as the same value,
// both compact and indented.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <initializer_list>
#include <iostream>
#include <stdexcept>
#include <string>

#include "types/Json.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());
    types::Json value;

    try {
        value = types::Json::parse(input);
    } catch (const std::invalid_argument&) {
        return 0;
    }

    constexpr int compact = -1;
    constexpr int indented = 2;

    for (const int indent : {compact, indented}) {
        const std::string text = value.dump(indent);
        const types::Json reread = types::Json::parse(text);

        if (reread != value || reread.dump(indent) != text) {
            std::cerr << "Json did not read back what it wrote: " << text << '\n';
            std::abort();
        }
    }

    return 0;
}
