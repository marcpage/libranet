// libFuzzer calls this with inputs it makes up. StructuredField::parse()
// turns down text it cannot read with std::invalid_argument; anything else it
// does with an input, such as crash, hang, or throw something else, is a
// fault. What it reads, it must write as text it reads back as the same
// dictionary.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

#include "http/StructuredField.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());
    http::StructuredField field;

    try {
        field = http::StructuredField::parse(input);
    } catch (const std::invalid_argument&) {
        return 0;
    }

    const std::string text = field.to_string();
    const auto reread = http::StructuredField::parse(text);

    if (reread != field || reread.to_string() != text) {
        std::cerr << "StructuredField did not read back what it wrote: " << text << '\n';
        std::abort();
    }

    return 0;
}
