// libFuzzer calls this with inputs it makes up. NodeList::from_body() turns
// down an input that is not a node list, as JSON or as a zlib stream of JSON,
// with std::invalid_argument; anything else it does with an input, such as
// crash, hang, or throw something else, is a fault. What it reads, it must
// send as a body it reads back as the same list, and resolving what it reads
// must give a list it reads back the same too.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

#include "protocol/NodeList.hpp"

using protocol::NodeList;

namespace {

// Ends the program, as libFuzzer reports a fault, unless list's body reads
// back as the same list as its text does. A list read is in the order of its
// endpoints, which need not be the order of one resolved.
void check_reads_back(const NodeList& list) {
    const auto text = list.to_string();
    const auto read = NodeList::from_body(text).to_string();

    if (read.size() != text.size() || NodeList::from_body(list.body().text).to_string() != read) {
        std::cerr << "NodeList did not read back what it wrote: " << text << '\n';
        std::abort();
    }
}

} // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());
    NodeList list;

    try {
        list = NodeList::from_body(input);
    } catch (const std::invalid_argument&) {
        return 0;
    }

    check_reads_back(list);
    check_reads_back(list.resolved("203.0.113.42"));
    check_reads_back(list.resolved("::1"));
    return 0;
}
