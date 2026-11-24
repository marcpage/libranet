#pragma once

#include <cstddef>
#include <optional>
#include <string>

#include "Identifier.hpp"

namespace storage {

// Content newly stored, and how many bytes it takes as stored. node is the
// node it came from, this node for its own, and nothing for a client whose
// connection carries no verified signature.
struct DataStored {
    static constexpr auto name = "data.stored";

    // A plain struct, but for its conversion to text, which makes the check
    // take it for a class.
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
