// libFuzzer calls this with inputs it makes up. SeekList::from_body() turns
// down a body that is not a seek list, as JSON or as zlib-compressed JSON,
// with std::invalid_argument; anything else it does with an input, such as
// crash, hang, or throw something else, is a fault. What it reads, it must
// write as a body it reads back as the same list, and writes again the same.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>

#include "protocol/SeekList.hpp"

using protocol::SeekList;

namespace {

// The text of each entry.
template<typename Entry>
std::vector<std::string> texts(const std::vector<Entry>& entries) {
    std::vector<std::string> result;

    std::transform(entries.begin(), entries.end(), std::back_inserter(result),
                   [](const Entry& entry) { return entry.to_string(); });
    return result;
}

// Whether left and right seek the same, in the same order.
bool same(const SeekList& left, const SeekList& right) {
    return texts(left.data()) == texts(right.data())
           && texts(left.search()) == texts(right.search());
}

} // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    // Far more than the fuzzer's inputs, of a few KiB, hold, but little
    // enough that a list read is never too long to be written whole.
    constexpr std::size_t decompressed_limit = 64UL * 1024UL;
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());
    SeekList list;

    try {
        list = SeekList::from_body(input, decompressed_limit);
    } catch (const std::invalid_argument&) {
        return 0;
    }

    const auto body = list.body();
    const auto reread = SeekList::from_body(body.text, decompressed_limit);

    if (!same(reread, list) || reread.body().text != body.text) {
        std::cerr << "SeekList did not read back what it wrote, of " << body.text.size()
                  << " bytes\n";
        std::abort();
    }

    return 0;
}
