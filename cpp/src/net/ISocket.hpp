#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>

#include "Address.hpp"

namespace net {

class ISocket {
public:
    using Ptr = std::unique_ptr<ISocket>;
    using Milliseconds = std::chrono::milliseconds;

    static Ptr bind(const Address& address);
    static Ptr connect(const Address& address);

    virtual ~ISocket() = default;
    ISocket(const ISocket&) = delete;
    ISocket& operator=(const ISocket&) = delete;

    virtual void listen(int backlog) = 0;
    [[nodiscard]] virtual std::unique_ptr<ISocket> accept() = 0;

    void send_all(std::string_view data);
    [[nodiscard]] std::vector<std::byte> read_exact(std::size_t size);
    [[nodiscard]] std::string read_line(char delimiter = '\n');
    virtual std::size_t send(std::string_view data) = 0;
    virtual bool receive(std::vector<std::byte>& buffer) = 0;

    [[nodiscard]] virtual Address local_address() const = 0;
    [[nodiscard]] virtual Address remote_address() const = 0;
    [[nodiscard]] virtual bool is_open() const noexcept = 0;
    virtual void set_timeout(Milliseconds timeout);
    virtual void set_receive_timeout(Milliseconds timeout) = 0;
    virtual void set_send_timeout(Milliseconds timeout) = 0;
    virtual void close() = 0;

protected:
    ISocket() = default;
};

inline void ISocket::set_timeout(Milliseconds timeout) {
    set_receive_timeout(timeout);
    set_send_timeout(timeout);
}

} // namespace net
