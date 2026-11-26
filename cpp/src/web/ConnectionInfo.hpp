#pragma once

#include <cstddef>
#include <optional>
#include <utility>

#include "net/Address.hpp"
#include "storage/Identifier.hpp"

namespace web {

// What a handler knows of the connection a request came on, used only by the
// thread serving it.
class ConnectionInfo {
public:
    ConnectionInfo(net::Address remote_address, net::Address local_address);

    // Which resolving localhost needs (HTTP API §10.2).
    [[nodiscard]] const net::Address& remote_address() const noexcept;
    // The address of this machine the connection reached. A client on this
    // machine that connects to one of its network addresses comes from that
    // same address (HTTP API §2.3, §2.4).
    [[nodiscard]] const net::Address& local_address() const noexcept;

    // The one being answered included.
    [[nodiscard]] std::size_t requests() const noexcept;
    void add_a_request() noexcept;

    // Once verified (HTTP API §11), and nothing before.
    [[nodiscard]] const std::optional<storage::Identifier>& node() const noexcept;
    void set_node(storage::Identifier new_node);

    [[nodiscard]] bool is_closing() const noexcept;
    // Once the answer being made is sent, saying so in its Connection header.
    void close_after_answer() noexcept;

private:
    net::Address m_remote_address;
    net::Address m_local_address;
    std::size_t m_requests = 0;
    std::optional<storage::Identifier> m_node;
    bool m_closing = false;
};

inline ConnectionInfo::ConnectionInfo(net::Address remote_address, net::Address local_address)
    : m_remote_address(std::move(remote_address)), m_local_address(std::move(local_address)) {}

inline const net::Address& ConnectionInfo::remote_address() const noexcept {
    return m_remote_address;
}

inline const net::Address& ConnectionInfo::local_address() const noexcept {
    return m_local_address;
}

inline std::size_t ConnectionInfo::requests() const noexcept {
    return m_requests;
}

inline void ConnectionInfo::add_a_request() noexcept {
    ++m_requests;
}

inline const std::optional<storage::Identifier>& ConnectionInfo::node() const noexcept {
    return m_node;
}

inline void ConnectionInfo::set_node(storage::Identifier new_node) {
    m_node = std::move(new_node);
}

inline bool ConnectionInfo::is_closing() const noexcept {
    return m_closing;
}

inline void ConnectionInfo::close_after_answer() noexcept {
    m_closing = true;
}

} // namespace web
