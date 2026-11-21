#pragma once

#include <cstddef>
#include <optional>
#include <string>

#include "Identifier.hpp"

namespace storage {

// Content newly stored, posted under data.stored, as the Python node names
// it: what it is, how many bytes it takes as stored, and the node it came
// from, this node for its own. The node is nothing for content from a
// client that is no node, one whose connection carries no verified
// signature.
struct DataStored {
    static constexpr auto name = "data.stored";

    // An event is a plain struct, read by whatever takes it. Its conversion
    // to text, which msg::Message says what it holds with, is a member, as a
    // conversion must be, so the check takes it for a class.
    // NOLINTBEGIN(*-non-private-member-variables-in-classes)
    Identifier identifier;
    std::optional<Identifier> node;
    std::size_t size = 0;
    // NOLINTEND(*-non-private-member-variables-in-classes)

    explicit operator std::string() const;
};

inline DataStored::operator std::string() const {
    return identifier.to_string() + ", " + std::to_string(size) + " bytes, from "
           + (node ? node->to_string() : std::string("no node"));
}

} // namespace storage
