#pragma once

#include <string>
#include <vector>

#include "HashPrefix.hpp"
#include "Identifier.hpp"

namespace storage {

// What a peer answered a search for prefix with, or named as matching one the
// node seeks (HttpApi §10.7.2), best match first.
struct SearchAnswered {
    static constexpr auto name = "data.search_answered";

    // A plain struct, but for its conversion to text, which makes the check
    // take it for a class.
    // NOLINTBEGIN(*-non-private-member-variables-in-classes)
    HashPrefix prefix;
    Identifier node;
    std::vector<Identifier> results;
    // NOLINTEND(*-non-private-member-variables-in-classes)

    explicit operator std::string() const;
};

inline SearchAnswered::operator std::string() const {
    return prefix.to_string() + " from " + node.to_string() + ", "
           + std::to_string(results.size()) + " results";
}

} // namespace storage
