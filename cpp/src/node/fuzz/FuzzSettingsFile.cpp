// libFuzzer calls this with inputs it makes up. SettingsFile::parse() turns
// down text it cannot read with std::invalid_argument; anything else it does
// with an input, such as crash, hang, or throw something else, is a fault.
// What it reads, it must write as text it reads back as the same settings.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

#include "node/SettingsFile.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());
    node::SettingsFile settings;

    try {
        settings = node::SettingsFile::parse(input);
    } catch (const std::invalid_argument&) {
        return 0;
    }

    const std::string text = settings.to_string();
    const auto reread = node::SettingsFile::parse(text);

    if (reread != settings || reread.to_string() != text) {
        std::cerr << "SettingsFile did not read back what it wrote: " << text << '\n';
        std::abort();
    }

    return 0;
}
