// libFuzzer calls this with inputs it makes up. Url::parse turns down an
// input that is not a URL by returning nothing; anything else it does with
// an input, such as crash, hang, or throw, is a fault. A URL it parses is
// decoded, and written back out as text it reads back as the same URL.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>

#include "http/Url.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());

    const auto url = http::Url::parse(input);

    if (!url.has_value()) {
        return 0;
    }

    (void)url->username();
    (void)url->password();
    (void)url->path();
    (void)url->fragment();
    (void)url->effective_port();

    const std::string text = url->to_string();
    const auto reread = http::Url::parse(text);

    if (!reread.has_value() || *reread != *url || reread->to_string() != text) {
        std::cerr << "Url did not read back what it wrote: " << text << '\n';
        std::abort();
    }

    return 0;
}
