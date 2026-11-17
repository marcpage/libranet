#pragma once

#if __APPLE__

#include <cstddef>
#include <string>
#include <string_view>
#include <vector>

#include <CoreFoundation/CoreFoundation.h>

#include "CFObject.hpp"

namespace crypto {

// Core Foundation's data, made from and read as the bytes and text this
// library keeps. Core Foundation takes and gives bytes as UInt8, an unsigned
// char, and this library keeps them as std::byte or char. Each of the three
// may be read as any other, so a pointer to one is passed as a pointer to
// another.
class CFBytes {
public:
    // New data holding a copy of bytes or text; empty if Core Foundation
    // cannot make it.
    [[nodiscard]] static CFObject<CFDataRef> of(const std::vector<std::byte>& bytes);
    [[nodiscard]] static CFObject<CFDataRef> of(std::string_view text);
    // A copy of what data holds.
    [[nodiscard]] static std::vector<std::byte> bytes_in(CFDataRef data);
    [[nodiscard]] static std::string text_in(CFDataRef data);

    CFBytes() = delete;
    ~CFBytes() = delete;
    CFBytes(const CFBytes&) = delete;
    CFBytes& operator=(const CFBytes&) = delete;
    CFBytes(CFBytes&&) = delete;
    CFBytes& operator=(CFBytes&&) = delete;

private:
    [[nodiscard]] static CFObject<CFDataRef> _copy(const UInt8* start, std::size_t size);
};

// The casts, and the pointer arithmetic over what Core Foundation holds, are
// the ones the class comment describes.
// NOLINTBEGIN(cppcoreguidelines-pro-type-reinterpret-cast)
// NOLINTBEGIN(cppcoreguidelines-pro-bounds-pointer-arithmetic)
inline CFObject<CFDataRef> CFBytes::of(const std::vector<std::byte>& bytes) {
    return _copy(reinterpret_cast<const UInt8*>(bytes.data()), bytes.size());
}

inline CFObject<CFDataRef> CFBytes::of(std::string_view text) {
    return _copy(reinterpret_cast<const UInt8*>(text.data()), text.size());
}

inline std::vector<std::byte> CFBytes::bytes_in(CFDataRef data) {
    const auto* start = reinterpret_cast<const std::byte*>(CFDataGetBytePtr(data));

    return {start, start + CFDataGetLength(data)};
}

inline std::string CFBytes::text_in(CFDataRef data) {
    return {reinterpret_cast<const char*>(CFDataGetBytePtr(data)),
            static_cast<std::size_t>(CFDataGetLength(data))};
}
// NOLINTEND(cppcoreguidelines-pro-bounds-pointer-arithmetic)
// NOLINTEND(cppcoreguidelines-pro-type-reinterpret-cast)

inline CFObject<CFDataRef> CFBytes::_copy(const UInt8* start, std::size_t size) {
    return CFObject<CFDataRef>(CFDataCreate(kCFAllocatorDefault, start, static_cast<CFIndex>(size)),
                               CFObject<CFDataRef>::Policy::TakeOwnership);
}

} // namespace crypto

#endif // __APPLE__
