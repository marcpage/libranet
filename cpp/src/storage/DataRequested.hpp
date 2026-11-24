#pragma once

#include <string>

#include "Identifier.hpp"

namespace storage {

// Content asked for, held or not.
struct DataRequested {
    static constexpr auto name = "data.requested";

    // A plain struct, but for its conversion to text, which makes the check
    // take it for a class.
    // NOLINTBEGIN(*-non-private-member-variables-in-classes)
    Identifier identifier;
    // NOLINTEND(*-non-private-member-variables-in-classes)

    explicit operator std::string() const;
};

inline DataRequested::operator std::string() const {
    return identifier.to_string();
}

} // namespace storage
