// libFuzzer calls this with inputs it makes up. RsaDer turns down an input
// that is not an RSA key in SubjectPublicKeyInfo, or in PKCS #8, with
// std::invalid_argument; anything else it does with an input, such as crash,
// hang, or throw something else, is a fault. What it reads, it must write
// back byte for byte: DER has one encoding for each value.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <iterator>
#include <stdexcept>

#include "crypto/RsaDer.hpp"

using crypto::RsaDer;

namespace {

// Reads input with read, and if it is a key, writes it back with write.
void check(const RsaDer::Data& input, RsaDer::Data (*read)(const RsaDer::Data&),
           RsaDer::Data (*write)(const RsaDer::Data&), const char* form) {
    RsaDer::Data key;

    try {
        key = read(input);
    } catch (const std::invalid_argument&) {
        return;
    }

    if (write(key) != input) {
        std::cerr << form << " did not write back what it read\n";
        std::abort();
    }
}

} // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    RsaDer::Data input(size);
    std::transform(data, std::next(data, static_cast<std::ptrdiff_t>(size)), input.begin(),
                   [](std::uint8_t byte) { return static_cast<std::byte>(byte); });

    check(input, &RsaDer::pkcs1_from_spki, &RsaDer::spki_from_pkcs1, "SubjectPublicKeyInfo");
    check(input, &RsaDer::pkcs1_from_pkcs8, &RsaDer::pkcs8_from_pkcs1, "PKCS #8");
    return 0;
}
