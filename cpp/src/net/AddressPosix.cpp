#if defined(__linux__) || defined(__APPLE__)

#include <algorithm>
#include <arpa/inet.h>
#include <array>
#include <ifaddrs.h>
#include <net/if.h>
#include <netdb.h>
#include <stdexcept>
#include <string>
#include <string_view>
#include <sys/socket.h>
#include <sys/types.h>
#include <vector>

#include "Address.hpp"

namespace {

[[nodiscard]] addrinfo* fetch_address_info(const std::string& host,
                                           net::Address::Port port) {
    addrinfo hints{};
    hints.ai_family = AF_UNSPEC; // Allow IPv4 or IPv6
    hints.ai_socktype = SOCK_STREAM;

    addrinfo* res = nullptr;
    const auto port_str = std::to_string(port);

    const auto status = ::getaddrinfo(host.c_str(), port_str.c_str(), &hints, &res);

    if (status != 0) {
        throw std::runtime_error(std::string("Failed to resolve host: ")
                                 + gai_strerror(status));
    }

    return res;
}

[[nodiscard]] std::string get_numeric_ip(const addrinfo* info) {
    std::array<char, NI_MAXHOST> ip_buf{};
    const int error = getnameinfo(info->ai_addr, info->ai_addrlen,
                                  ip_buf.data(), static_cast<socklen_t>(ip_buf.size()),
                                  nullptr, 0, NI_NUMERICHOST);
    if (error == 0) {
        return ip_buf.data();
    }

    return {};
}

[[nodiscard]] std::string get_reverse_dns_name(const addrinfo* info,
                                               const std::string& numeric_ip) {
    std::array<char, NI_MAXHOST> host_buf{};
    const int error = getnameinfo(info->ai_addr, info->ai_addrlen,
                                  host_buf.data(), static_cast<socklen_t>(host_buf.size()),
                                  nullptr, 0, 0);
    if (error == 0) {
        std::string dns_name = host_buf.data();

        if (!dns_name.empty() && dns_name != numeric_ip) {
            return dns_name;
        }
    }

    return {};
}

void add_unique(std::string_view value, std::vector<std::string>& results) {
    if (!value.empty() && std::find(results.begin(), results.end(), value) == results.end()) {
        results.emplace_back(value);
    }
}

[[nodiscard]] ifaddrs* fetch_interface_addresses() {
    ifaddrs* ifaddr = nullptr;

    if (::getifaddrs(&ifaddr) == -1) {
        throw std::runtime_error("Failed to retrieve local network interfaces");
    }

    return ifaddr;
}

[[nodiscard]] bool is_supported_socket_family(int family) {
    return family == AF_INET || family == AF_INET6;
}

[[nodiscard]] socklen_t get_sockaddr_len(int family) {
    return (family == AF_INET) ? sizeof(sockaddr_in) : sizeof(sockaddr_in6);
}

[[nodiscard]] std::string extract_interface_ip(const ifaddrs* ifa) {
    const int family = ifa->ifa_addr->sa_family;

    if (!is_supported_socket_family(family)) {
        return {};
    }

    std::array<char, NI_MAXHOST> host{};
    const socklen_t len = get_sockaddr_len(family);

    const int error = getnameinfo(ifa->ifa_addr, len, host.data(), static_cast<socklen_t>(host.size()),
                                  nullptr, 0, NI_NUMERICHOST);
    if (error != 0) {
        return {};
    }

    if (family != AF_INET6) {
        return host.data();
    }

    // Strip IPv6 interface scope IDs (e.g., "%en0")
    const std::string_view address = host.data();

    return std::string(address.substr(0, address.find('%')));
}

} // namespace

namespace net {

std::vector<std::string> Address::resolve() const {
    std::vector<std::string> results;
    addrinfo* res = fetch_address_info(m_host, m_port);

    for (const addrinfo* entry = res; entry != nullptr; entry = entry->ai_next) {
        const auto numeric_ip = get_numeric_ip(entry);

        add_unique(numeric_ip, results);
        add_unique(get_reverse_dns_name(entry, numeric_ip), results);
    }

    freeaddrinfo(res);
    return results;
}

std::vector<std::string> Address::get_local_addresses() {
    ifaddrs* ifaddr = fetch_interface_addresses();
    std::vector<std::string> addresses;

    for (const ifaddrs* ifa = ifaddr; ifa != nullptr; ifa = ifa->ifa_next) {
        if (ifa->ifa_addr == nullptr) {
            continue;
        }

        add_unique(extract_interface_ip(ifa), addresses);
    }

    freeifaddrs(ifaddr);
    return addresses;
}


} // namespace net

#endif // __linux__ || __APPLE__
