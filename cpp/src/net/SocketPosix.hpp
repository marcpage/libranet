#pragma once

#include "ISocket.hpp"

namespace net {

class SocketPosix final : public ISocket {
public:
    static ISocket::Ptr bind(const Address& address);
    static ISocket::Ptr connect(const Address& address);

    ~SocketPosix() override;

    void listen(int backlog) override;
    ISocket::Ptr accept() override;
    std::size_t send(std::string_view data) override;
    bool receive(std::vector<std::byte>& buffer) override;
    [[nodiscard]] Address local_address() const override;
    [[nodiscard]] Address remote_address() const override;
    [[nodiscard]] bool is_open() const noexcept override;
    void set_receive_timeout(ISocket::Milliseconds timeout) override;
    void set_send_timeout(ISocket::Milliseconds timeout) override;
    void close() override;

private:
    explicit SocketPosix(int socket_fd) noexcept;

    SocketPosix() = delete;
    SocketPosix(const SocketPosix&) = delete;
    SocketPosix& operator=(const SocketPosix&) = delete;

    int m_socket_fd;
};

} // namespace net
