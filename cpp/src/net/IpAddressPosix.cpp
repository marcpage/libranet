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

using std::array;
using std::copy;
using std::generic_category;
using std::next;
using std::nullopt;
using std::optional;
using std::string;
using std::string_view;
using std::system_error;
using std::uint8_t;

namespace {

// As many bytes as an IPv4 address has.
using Ipv4Bytes = array<uint8_t, sizeof(in_addr)>;

// What inet_ntop() writes for the address of family whose bytes start at
// bytes. Throws system_error when it writes nothing.
string text_of(int family, const uint8_t* bytes) {
    array<char, INET6_ADDRSTRLEN> text{};

    if (nullptr
        == ::inet_ntop(family, bytes, text.data(), static_cast<socklen_t>(text.size()))) {
        throw system_error(errno, generic_category(), "inet_ntop");
    }

    return {text.data()};
}

} // namespace

namespace net {

optional<IpAddress> IpAddress::parse(string_view text) {
    // inet_pton() reads text up to a NUL, which would let what follows one
    // through unread.
    if (text.find('\0') != string_view::npos) {
        return nullopt;
    }

    const string terminated(text);
    Ipv4Bytes ipv4{};
    Bytes bytes = mapped_prefix;

    if (1 == ::inet_pton(AF_INET, terminated.c_str(), ipv4.data())) {
        copy(ipv4.begin(), ipv4.end(), next(bytes.begin(), mapped_prefix_size));
        return IpAddress(bytes);
    }

    if (1 == ::inet_pton(AF_INET6, terminated.c_str(), bytes.data())) {
        return IpAddress(bytes);
    }

    return nullopt;
}

string IpAddress::to_string() const {
    if (!_is_ipv4()) {
        return text_of(AF_INET6, m_bytes.data());
    }

    Ipv4Bytes ipv4{};

    copy(next(m_bytes.begin(), mapped_prefix_size), m_bytes.end(), ipv4.begin());
    return text_of(AF_INET, ipv4.data());
}

} // namespace net

#endif // __linux__ || __APPLE__
