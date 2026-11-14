// libFuzzer calls this with inputs it makes up. Url::parse turns down an
// input that is not a URL by returning nothing; anything else it does with
// an input, such as crash, hang, or throw, is a fault. A URL it parses is
// decoded and written back out, which must not fault either.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <string>

#include "http/Url.hpp"

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());

    const auto url = http::Url::parse(input);

    if (url.has_value()) {
        static_cast<void>(url->username());
        static_cast<void>(url->password());
        static_cast<void>(url->path());
        static_cast<void>(url->fragment());
        static_cast<void>(url->effective_port());
        static_cast<void>(url->to_string());
    }

    return 0;
}
