#if defined(__linux__) || defined(__APPLE__)

#include "IpAddress.hpp"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdint>
#include <iterator>
#include <optional>
#include <string>
#include <string_view>
#include <system_error>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>

namespace {

// As many bytes as an IPv4 address has.
using Ipv4Bytes = std::array<std::uint8_t, sizeof(in_addr)>;

// What inet_ntop() writes for the address of family whose bytes start at
// bytes. Throws std::system_error when it writes nothing.
std::string text_of(int family, const std::uint8_t* bytes) {
    std::array<char, INET6_ADDRSTRLEN> text{};

    if (nullptr
        == ::inet_ntop(family, bytes, text.data(), static_cast<socklen_t>(text.size()))) {
        throw std::system_error(errno, std::generic_category(), "inet_ntop");
    }

    return {text.data()};
}

} // namespace

namespace net {

std::optional<IpAddress> IpAddress::parse(std::string_view text) {
    // inet_pton() reads text up to a NUL, which would let what follows one
    // through unread.
    if (text.find('\0') != std::string_view::npos) {
        return std::nullopt;
    }

    const std::string terminated(text);
    Ipv4Bytes ipv4{};
    Bytes bytes = mapped_prefix;

    if (1 == ::inet_pton(AF_INET, terminated.c_str(), ipv4.data())) {
        std::copy(ipv4.begin(), ipv4.end(), std::next(bytes.begin(), mapped_prefix_size));
        return IpAddress(bytes);
    }

    if (1 == ::inet_pton(AF_INET6, terminated.c_str(), bytes.data())) {
        return IpAddress(bytes);
    }

    return std::nullopt;
}

std::string IpAddress::to_string() const {
    if (!_is_ipv4()) {
        return text_of(AF_INET6, m_bytes.data());
    }

    Ipv4Bytes ipv4{};

    std::copy(std::next(m_bytes.begin(), mapped_prefix_size), m_bytes.end(), ipv4.begin());
    return text_of(AF_INET, ipv4.data());
}

} // namespace net

#endif // __linux__ || __APPLE__
