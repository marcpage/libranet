#pragma once

#include <cstddef>
#include <string>

#include "Identifier.hpp"

namespace storage {

// Content the node no longer holds, posted under data.deleted, as the Python
// node names it: what it is, and how many bytes it took as stored.
struct DataDeleted {
    static constexpr auto name = "data.deleted";

    // An event is a plain struct, read by whatever takes it. Its conversion
    // to text, which msg::Message says what it holds with, is a member, as a
    // conversion must be, so the check takes it for a class.
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
