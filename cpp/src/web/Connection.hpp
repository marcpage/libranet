#pragma once

#include <cstddef>
#include <optional>
#include <utility>

#include "net/Address.hpp"
#include "storage/Identifier.hpp"

namespace web {

// What a handler knows of the connection a request came on. The server makes
// one as it takes a connection, and hands it to each handler with the request
// and the response, on the thread serving that connection alone.
class Connection {
public:
    explicit Connection(net::Address remote_address);

    // Where the connection came from, which resolving localhost needs
    // (HTTP API §10.2).
    [[nodiscard]] const net::Address& remote_address() const noexcept;

    // How many requests the connection has carried, the one being answered
    // included.
    [[nodiscard]] std::size_t requests() const noexcept;
    // Counts a request read from the connection, as the server does before
    // it answers each.
    void count_request() noexcept;

    // The node whose signature the connection carries, once verified
    // (HTTP API §11), and nothing before.
    [[nodiscard]] const std::optional<storage::Identifier>& node() const noexcept;
    void set_node(storage::Identifier new_node);

    // Whether the server closes the connection once the answer being made is
    // sent.
    [[nodiscard]] bool is_closing() const noexcept;
    // Has the server close the connection once the answer being made is
    // sent, saying so in its Connection header.
    void close_after_answer() noexcept;

private:
    net::Address m_remote_address;
    std::size_t m_requests = 0;
    std::optional<storage::Identifier> m_node;
    bool m_closing = false;
};

inline Connection::Connection(net::Address remote_address)
    : m_remote_address(std::move(remote_address)) {}

inline const net::Address& Connection::remote_address() const noexcept {
    return m_remote_address;
}

inline std::size_t Connection::requests() const noexcept {
    return m_requests;
}

inline void Connection::count_request() noexcept {
    ++m_requests;
}

inline const std::optional<storage::Identifier>& Connection::node() const noexcept {
    return m_node;
}

inline void Connection::set_node(storage::Identifier new_node) {
    m_node = std::move(new_node);
}

inline bool Connection::is_closing() const noexcept {
    return m_closing;
}

inline void Connection::close_after_answer() noexcept {
    m_closing = true;
}

} // namespace web
