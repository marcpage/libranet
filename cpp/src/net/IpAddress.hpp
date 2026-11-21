#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <iterator>
#include <optional>
#include <string>
#include <string_view>

namespace net {

// An IP address, held as an IPv6 address's 16 bytes, with an IPv4 address as
// the IPv4-mapped IPv6 address of it (RFC 4291 §2.5.5.2). So the IPv4 client
// a dual-stack listener reports as ::ffff:203.0.113.42 is 203.0.113.42, as
// it is to a node without IPv6.
class IpAddress {
public:
    // The address text names, as inet_pton() reads one: IPv4 in dotted
    // decimal, and IPv6 as RFC 4291 §2.2 writes it, without brackets or a
    // zone. Nothing when text names none, as a host name does.
    [[nodiscard]] static std::optional<IpAddress> parse(std::string_view text);

    // In 127.0.0.0/8 (RFC 1122 §3.2.1.3), or ::1 (RFC 4291 §2.5.3).
    [[nodiscard]] bool is_loopback() const;
    // As inet_ntop() writes it: IPv4 in dotted decimal, and IPv6 as short as
    // it can be, in lower case (RFC 5952).
    [[nodiscard]] std::string to_string() const;
    // As a URL names it as its host: IPv6 in brackets (RFC 3986 §3.2.2).
    [[nodiscard]] std::string url_host() const;

private:
    // As many bytes as an IPv6 address has (RFC 4291 §2).
    static constexpr std::size_t ipv6_size = 16;

    using Bytes = std::array<std::uint8_t, ipv6_size>;

    // ::ffff:0:0/96, the IPv4-mapped addresses: their first 12 bytes are
    // these, and their last 4 the IPv4 address.
    static constexpr Bytes mapped_prefix{0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xFF, 0xFF, 0, 0, 0, 0};
    static constexpr std::ptrdiff_t mapped_prefix_size = 12;
    static constexpr Bytes ipv6_loopback{0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1};
    // The first byte of every IPv4 loopback address.
    static constexpr std::uint8_t ipv4_loopback = 127;

    Bytes m_bytes;

    explicit IpAddress(const Bytes& bytes);

    // Whether this is an IPv4 address, held as the IPv6 address that maps it.
    [[nodiscard]] bool _is_ipv4() const;
};

inline IpAddress::IpAddress(const Bytes& bytes)
    : m_bytes(bytes) {}

inline bool IpAddress::is_loopback() const {
    if (_is_ipv4()) {
        return ipv4_loopback == *std::next(m_bytes.begin(), mapped_prefix_size);
    }

    return ipv6_loopback == m_bytes;
}

inline std::string IpAddress::url_host() const {
    if (_is_ipv4()) {
        return to_string();
    }

    return "[" + to_string() + "]";
}

inline bool IpAddress::_is_ipv4() const {
    return std::equal(mapped_prefix.begin(), std::next(mapped_prefix.begin(), mapped_prefix_size),
                      m_bytes.begin());
}

} // namespace net
