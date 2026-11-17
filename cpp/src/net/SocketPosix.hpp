#pragma once

#include "ISocket.hpp"

namespace net {

class SocketPosix final : public ISocket {
public:
    // cppcheck-suppress duplInheritedMember
    [[nodiscard]] static ISocket::Ptr bind(const Address& address);
    // cppcheck-suppress duplInheritedMember
    [[nodiscard]] static ISocket::Ptr connect(const Address& address);

    SocketPosix() = delete;
    ~SocketPosix() override;
    SocketPosix(const SocketPosix&) = delete;
    SocketPosix& operator=(const SocketPosix&) = delete;
    SocketPosix(SocketPosix&&) = delete;
    SocketPosix& operator=(SocketPosix&&) = delete;

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

    int m_socket_fd;
};

} // namespace net
