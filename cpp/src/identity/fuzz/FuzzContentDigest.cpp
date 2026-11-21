// libFuzzer calls this with inputs it makes up. ContentDigest::parse() turns
// down text it cannot read with std::invalid_argument; anything else it does
// with an input, such as crash, hang, or throw something else, is a fault.
// What it reads, it must check against a body without throwing, and write as
// text it reads back as the same field.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>

#include "identity/ContentDigest.hpp"

using identity::ContentDigest;

namespace {

// The field input is, or nothing when parse() turns it down.
std::optional<ContentDigest> parsed(const std::string& input) {
    try {
        return ContentDigest::parse(input);
    } catch (const std::invalid_argument&) {
        return std::nullopt;
    }
}

} // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());
    const auto read = parsed(input);

    if (!read) {
        return 0;
    }

    (void)read->matches(input);

    const auto text = read->to_string();

    if (ContentDigest::parse(text).to_string() != text) {
        std::cerr << "ContentDigest did not read back what it wrote: " << text << '\n';
        std::abort();
    }

    return 0;
}
