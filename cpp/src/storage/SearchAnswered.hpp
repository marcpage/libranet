#pragma once

#include <string>
#include <vector>

#include "HashPrefix.hpp"
#include "Identifier.hpp"

namespace storage {

// What a peer answered a search for prefix with, or named as matching one
// the node seeks (HttpApi §10.7.2), posted under data.search_answered, as
// the Python node names it: the node that named them, and what it named,
// best match first, as it named them. What takes it keeps only those that
// match prefix closely enough for a search to return them.
struct SearchAnswered {
    static constexpr auto name = "data.search_answered";

    // An event is a plain struct, read by whatever takes it. Its conversion
    // to text, which msg::Message says what it holds with, is a member, as a
    // conversion must be, so the check takes it for a class.
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
