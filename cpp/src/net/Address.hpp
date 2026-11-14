#pragma once

#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace net {

class Address {
public:
    using Port = std::uint16_t;

    [[nodiscard]] static Address parse(std::string_view endpoint);

    Address(std::string_view host, Port port);

    [[nodiscard]] const std::string& host() const noexcept;
    [[nodiscard]] Port port() const noexcept;
    [[nodiscard]] std::string to_string() const;
    [[nodiscard]] std::vector<std::string> resolve() const;
    [[nodiscard]] static std::vector<std::string> get_local_addresses();

private:
    std::string m_host;
    Port m_port;
};

inline Address Address::parse(std::string_view endpoint) {
    // Look for the last colon to separate host and port (handles IPv6 vs IPv4)
    const auto pos = endpoint.rfind(':');

    if (pos == std::string_view::npos) {
        throw std::invalid_argument("Invalid address format, missing port");
    }

    std::string_view host = endpoint.substr(0, pos);
    const std::string_view port_str = endpoint.substr(pos + 1);

    // Strip brackets if it's an IPv6 address like [::1]:80
    if (!host.empty() && host.front() == '[' && host.back() == ']') {
        host = host.substr(1, host.size() - 2);
    }

    const Port port = static_cast<Port>(std::stoi(std::string(port_str)));
    return {host, port};
}

inline Address::Address(std::string_view host, Port port)
    : m_host(host), m_port(port) {}

inline const std::string& Address::host() const noexcept {
    return m_host;
}

inline Address::Port Address::port() const noexcept {
    return m_port;
}

inline std::string Address::to_string() const {
    // Basic formatting handling IPv6 vs IPv4 layout if needed
    if (m_host.find(':') != std::string::npos) {
        return "[" + m_host + "]:" + std::to_string(m_port);
    }

    return m_host + ":" + std::to_string(m_port);
}

} // namespace net
