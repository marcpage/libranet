#if defined(__linux__) || defined(__APPLE__)

#include <algorithm>
#include <arpa/inet.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netdb.h>
#include <stdexcept>
#include <sys/socket.h>
#include <sys/types.h>

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

[[nodiscard]] std::string get_numeric_ip(const addrinfo* p) {
    char ip_buf[NI_MAXHOST];
    const int error = getnameinfo(p->ai_addr, p->ai_addrlen,
                                  ip_buf, sizeof(ip_buf),
                                  nullptr, 0, NI_NUMERICHOST);
    if (error == 0) {
        return ip_buf;
    }

    return {};
}

[[nodiscard]] std::string get_reverse_dns_name(const addrinfo* p,
                                               const std::string& numeric_ip) {
    char host_buf[NI_MAXHOST];
    const int error = getnameinfo(p->ai_addr, p->ai_addrlen,
                                  host_buf, sizeof(host_buf),
                                  nullptr, 0, 0);
    if (error == 0) {
        std::string dns_name = host_buf;

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

    char host[NI_MAXHOST];
    const socklen_t len = get_sockaddr_len(family);

    const int error = getnameinfo(ifa->ifa_addr, len, host, sizeof(host),
                                  nullptr, 0, NI_NUMERICHOST);
    if (error != 0) {
        return {};
    }

    if (family != AF_INET6) {
        return host;
    }

    const std::string ip = host;

    // Strip IPv6 interface scope IDs (e.g., "%en0")
    const auto pos = ip.find('%');

    if (pos != std::string::npos) {
        return ip.substr(0, pos);
    }

    return ip;
}

} // anonymous

namespace net {

std::vector<std::string> Address::resolve() const {
    std::vector<std::string> results;
    addrinfo* res = fetch_address_info(m_host, m_port);

    for (const addrinfo* p = res; p != nullptr; p = p->ai_next) {
        const auto ip = get_numeric_ip(p);

        add_unique(ip, results);
        add_unique(get_reverse_dns_name(p, ip), results);
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


} // net

#endif // __linux__ || __APPLE__
