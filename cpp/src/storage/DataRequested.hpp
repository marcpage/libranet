#pragma once

#include <string>

#include "Identifier.hpp"

namespace storage {

// Content asked for, held or not, posted under data.requested, as the
// Python node names it.
struct DataRequested {
    static constexpr auto name = "data.requested";

    // An event is a plain struct, read by whatever takes it. Its conversion
    // to text, which msg::Message says what it holds with, is a member, as a
    // conversion must be, so the check takes it for a class.
    // NOLINTBEGIN(*-non-private-member-variables-in-classes)
    Identifier identifier;
    // NOLINTEND(*-non-private-member-variables-in-classes)

    explicit operator std::string() const;
};

inline DataRequested::operator std::string() const {
    return identifier.to_string();
}

} // namespace storage
