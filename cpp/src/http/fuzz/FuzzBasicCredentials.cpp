// libFuzzer calls this with inputs it makes up. BasicCredentials::parse()
// turns down a field it cannot read with an empty optional; anything else it
// does with an input, such as crash, hang, or throw, is a fault. The input is
// read as a field, and as the credentials a field encodes, so that the
// checks of what is decoded are reached. What it reads, it must write as a
// field it reads back as the same credentials and the same field.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <optional>
#include <string>

#include "crypto/Base64.hpp"
#include "http/BasicCredentials.hpp"

using http::BasicCredentials;

namespace {

// The username and password, joined by a ':', that field gives, if it gives
// any, once they are known to be written as a field read back the same.
std::optional<std::string> read_back(const std::string& field) {
    const auto read = BasicCredentials::parse(field);

    if (!read) {
        return std::nullopt;
    }

    const auto text = read->to_string();
    const auto reread = BasicCredentials::parse(text);

    if (!reread || reread->username() != read->username()
        || reread->password() != read->password() || reread->to_string() != text) {
        std::cerr << "BasicCredentials did not read back what it wrote: " << text << '\n';
        std::abort();
    }

    return read->user_pass();
}

} // namespace

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    std::string input(size, '\0');
    std::copy_n(data, size, input.begin());
    const auto encoded = "Basic " + crypto::Base64::encode(input);

    (void)read_back(input);

    const auto user_pass = read_back(encoded);

    if (user_pass && *user_pass != input) {
        std::cerr << "BasicCredentials did not read what was encoded: " << encoded << '\n';
        std::abort();
    }

    return 0;
}
