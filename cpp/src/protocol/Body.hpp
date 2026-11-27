#pragma once

#include <string>

namespace protocol {

// A list as it is sent (HttpApi §6.1, §10.6, §10.7.1).
struct Body {
    std::string text;
    // As one zlib stream. A search's answer then says so with
    // Content-Encoding: deflate (HttpApi §6.1), and a node or seek list does
    // not (§10.6).
    bool compressed = false;
};

} // namespace protocol
