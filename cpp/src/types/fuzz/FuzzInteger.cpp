// libFuzzer calls this with inputs it makes up. Integer turns down text
// that is not a number with std::invalid_argument; anything else it does
// with an input, such as crash, hang, or throw something else, is a fault.
// What it reads, it must write as text it reads back as the same number.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

#include "types/Integer.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());
    types::Integer value;

    try {
        value = types::Integer(input);
    } catch (const std::invalid_argument&) {
        return 0;
    }

    const std::string text = value.to_string();
    const types::Integer reread(text);

    if (reread != value || reread.to_string() != text) {
        std::cerr << "Integer did not read back what it wrote: " << text << '\n';
        std::abort();
    }

    return 0;
}
