#if defined(__linux__) || defined(__APPLE__)

#include "Address.hpp"

#include <algorithm>
#include <array>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include <arpa/inet.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netdb.h>
#include <sys/socket.h>
#include <sys/types.h>

using net::Address;
using std::array;
using std::runtime_error;
using std::string;
using std::string_view;
using std::vector;

namespace {

[[nodiscard]] addrinfo* fetch_address_info(const string& host,
                                           Address::Port port) {
    addrinfo hints{};
    hints.ai_family = AF_UNSPEC; // Allow IPv4 or IPv6
    hints.ai_socktype = SOCK_STREAM;

    addrinfo* result = nullptr;
    const auto port_text = std::to_string(port);

    const auto status = ::getaddrinfo(host.c_str(), port_text.c_str(), &hints, &result);

    if (status != 0) {
        throw runtime_error(string("Failed to resolve host: ")
                            + gai_strerror(status));
    }

    return result;
}

[[nodiscard]] string numeric_ip_of(const addrinfo* info) {
    array<char, NI_MAXHOST> ip_text{};
    const int error = getnameinfo(info->ai_addr, info->ai_addrlen,
                                  ip_text.data(), static_cast<socklen_t>(ip_text.size()),
                                  nullptr, 0, NI_NUMERICHOST);

    if (0 == error) {
        return ip_text.data();
    }

    return {};
}

[[nodiscard]] string reverse_dns_name_of(const addrinfo* info,
                                         const string& numeric_ip) {
    array<char, NI_MAXHOST> host_text{};
    const int error = getnameinfo(info->ai_addr, info->ai_addrlen,
                                  host_text.data(), static_cast<socklen_t>(host_text.size()),
                                  nullptr, 0, 0);

    if (0 == error) {
        string dns_name = host_text.data();

        if (!dns_name.empty() && dns_name != numeric_ip) {
            return dns_name;
        }
    }

    return {};
}

void add_unique(string_view value, vector<string>& results) {
    if (!value.empty() && std::find(results.begin(), results.end(), value) == results.end()) {
        results.emplace_back(value);
    }
}

[[nodiscard]] ifaddrs* fetch_interface_addresses() {
    ifaddrs* interfaces = nullptr;

    if (-1 == ::getifaddrs(&interfaces)) {
        throw runtime_error("Failed to retrieve local network interfaces");
    }

    return interfaces;
}

[[nodiscard]] bool is_supported_socket_family(int family) {
    return AF_INET == family || AF_INET6 == family;
}

[[nodiscard]] socklen_t sockaddr_length_of(int family) {
    return (AF_INET == family) ? sizeof(sockaddr_in) : sizeof(sockaddr_in6);
}

[[nodiscard]] string extract_interface_ip(const ifaddrs* interface_entry) {
    const int family = interface_entry->ifa_addr->sa_family;

    if (!is_supported_socket_family(family)) {
        return {};
    }

    array<char, NI_MAXHOST> host{};
    const socklen_t length = sockaddr_length_of(family);

    const int error = getnameinfo(interface_entry->ifa_addr, length, host.data(),
                                  static_cast<socklen_t>(host.size()), nullptr, 0, NI_NUMERICHOST);

    if (error != 0) {
        return {};
    }

    if (family != AF_INET6) {
        return host.data();
    }

    // Strip IPv6 interface scope IDs (e.g., "%en0")
    const string_view address = host.data();

    return string(address.substr(0, address.find('%')));
}

} // namespace

namespace net {

vector<string> Address::resolve() const {
    vector<string> results;
    addrinfo* result = fetch_address_info(m_host, m_port);

    for (const addrinfo* entry = result; entry != nullptr; entry = entry->ai_next) {
        const auto numeric_ip = numeric_ip_of(entry);

        add_unique(numeric_ip, results);
        add_unique(reverse_dns_name_of(entry, numeric_ip), results);
    }

    freeaddrinfo(result);
    return results;
}

vector<string> Address::local_addresses() {
    ifaddrs* interfaces = fetch_interface_addresses();
    vector<string> addresses;

    for (const ifaddrs* interface_entry = interfaces;
         interface_entry != nullptr;
         interface_entry = interface_entry->ifa_next) {
        if (nullptr == interface_entry->ifa_addr) {
            continue;
        }

        add_unique(extract_interface_ip(interface_entry), addresses);
    }

    freeifaddrs(interfaces);
    return addresses;
}

} // namespace net

#endif // __linux__ || __APPLE__
