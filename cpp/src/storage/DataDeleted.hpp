#pragma once

#include <cstddef>
#include <string>

#include "Identifier.hpp"

namespace storage {

// Content the node no longer holds, and how many bytes it took as stored.
struct DataDeleted {
    static constexpr auto name = "data.deleted";

    // A plain struct, but for its conversion to text, which makes the check
    // take it for a class.
    // NOLINTBEGIN(*-non-private-member-variables-in-classes)
    Identifier identifier;
    std::size_t size = 0;
    // NOLINTEND(*-non-private-member-variables-in-classes)

    explicit operator std::string() const;
};

inline DataDeleted::operator std::string() const {
    return identifier.to_string() + ", " + std::to_string(size) + " bytes";
}

} // namespace storage
