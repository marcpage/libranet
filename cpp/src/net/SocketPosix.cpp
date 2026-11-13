#if defined(__linux__) || defined(__APPLE__)

#include "SocketPosix.hpp"

#include <cerrno>
#include <cstring>
#include <limits>
#include <memory>
#include <stdexcept>
#include <system_error>

#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/types.h>
#include <unistd.h>

namespace {

#if defined(__APPLE__)
    constexpr int SEND_FLAGS = 0;
#elif defined(__linux__)
    constexpr int SEND_FLAGS = MSG_NOSIGNAL;
#endif

void assert_socket(bool condition, const char* operation, int error=errno) {
    if (!condition) {
        throw std::system_error(error, std::generic_category(), operation);
    }
}

template<typename E>
void assert_condition(bool condition, const std::string& message) {
    if (!condition) {
        throw E(message);
    }
}

struct AddrInfoDeleter {
    void operator()(addrinfo* value) const noexcept {
        if (value != nullptr) {
            ::freeaddrinfo(value);
        }
    }
};

using AddrInfoPtr=std::unique_ptr<addrinfo, AddrInfoDeleter>;

AddrInfoPtr resolve_address(const net::Address& address, int flags, int socket_type) {
    addrinfo hints{};

    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = socket_type;
    hints.ai_protocol = IPPROTO_TCP;
    hints.ai_flags = flags;

    const auto service = std::to_string(address.port());
    const auto* host = address.host().empty() ? nullptr : address.host().c_str();

    addrinfo* result = nullptr;
    const auto error = ::getaddrinfo(host, service.c_str(), &hints, &result);

    assert_condition<std::runtime_error>(
                    error == 0, std::string("getaddrinfo: ") + ::gai_strerror(error));

    return AddrInfoPtr(result);
}

int create_socket(const addrinfo& info) {
    const auto fd = ::socket(info.ai_family, info.ai_socktype, info.ai_protocol);

    if (fd < 0) {
        return -1;
    }

    const int enabled = 1;

    if (info.ai_socktype == SOCK_STREAM) {
        // Allow quick restart of a listening server after it has closed
        (void)::setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));

        // Disable Nagle's algorithm (common for request/response and low-latency use)
        (void)::setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &enabled, sizeof(enabled));

        // Detect dead peers on long-lived connections
        (void)::setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &enabled, sizeof(enabled));
    }

#if defined(__APPLE__)
    // Prevent SIGPIPE when writing to a closed peer (macOS / BSD)
    (void)::setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled));
#endif

    return fd;
}

net::Address address_from_sockaddr(const sockaddr* address, socklen_t length) {
    char host[NI_MAXHOST]{};
    char service[NI_MAXSERV]{};

    const auto error = ::getnameinfo(address, length,
                                    host, sizeof(host),
                                    service, sizeof(service),
                                    NI_NUMERICHOST | NI_NUMERICSERV);

    assert_condition<std::runtime_error>(
                    error == 0, std::string("getnameinfo: ") + ::gai_strerror(error));

    const auto port = std::stoul(service);

    assert_condition<std::runtime_error>(
                    port <= std::numeric_limits<net::Address::Port>::max(),
                    "Socket returned an invalid port");

    return net::Address(host, static_cast<net::Address::Port>(port));
}

enum Direction {DirectionSend, DirectionReceive};

int set_timeout(int socket_fd, Direction direction, net::ISocket::Milliseconds timeout) {
    const auto direction_flag = DirectionSend == direction ? SO_SNDTIMEO : SO_RCVTIMEO;
    timeval tv{};

    if (timeout.count() > 0) {
        tv.tv_sec  = static_cast<time_t>(timeout.count() / 1000);
        tv.tv_usec = static_cast<suseconds_t>((timeout.count() % 1000) * 1000);
    }

    return ::setsockopt(socket_fd, SOL_SOCKET, direction_flag, &tv, sizeof(tv));
}

template <typename Func>
net::Address query_socket_address(int socket_fd, Func&& socket_call,
                                                            const char* operation_name) {
    sockaddr_storage storage{};
    socklen_t length = sizeof(storage);

    const auto result = socket_call(socket_fd,
                                        reinterpret_cast<sockaddr*>(&storage), &length);

    assert_socket(result == 0, operation_name);
    return address_from_sockaddr(reinterpret_cast<const sockaddr*>(&storage), length);
}

} // namespace

namespace net {

ISocket::Ptr SocketPosix::bind(const Address& address) {
    const auto addresses = resolve_address(address, AI_PASSIVE, SOCK_STREAM);
    int last_error = EADDRNOTAVAIL;

    for (const auto* current = addresses.get();
            current != nullptr;
            current = current->ai_next) {
        const auto fd = create_socket(*current);

        if (fd < 0) {
            last_error = errno;
            continue;
        }

        if (::bind(fd, current->ai_addr, current->ai_addrlen) == 0) {
            return std::unique_ptr<SocketPosix>(new SocketPosix(fd));
        }

        last_error = errno;
        (void)::close(fd);
    }

    assert_socket(false, "bind", last_error);
    return nullptr;
}

ISocket::Ptr SocketPosix::connect(const Address& address) {
    const auto addresses = resolve_address(address, 0, SOCK_STREAM);
    int last_error = ECONNREFUSED;

    for (const addrinfo* current = addresses.get(); current != nullptr; current = current->ai_next) {
        const auto fd = create_socket(*current);

        if (fd < 0) {
            last_error = errno;
            continue;
        }

        int result;

        do {
            result = ::connect(fd, current->ai_addr, current->ai_addrlen);
        } while (result < 0 && errno == EINTR);

        if (result == 0) {
            return std::unique_ptr<SocketPosix>(new SocketPosix(fd));
        }

        last_error = errno;
        (void)::close(fd);
    }

    assert_socket(false, "connect", last_error);
    return nullptr;
}

SocketPosix::SocketPosix(int socket_fd) noexcept
    : m_socket_fd(socket_fd) {}

SocketPosix::~SocketPosix() {
    close();
}

void SocketPosix::listen(int backlog) {
    assert_condition<std::logic_error>(is_open(), "Cannot listen on a closed socket");
    assert_condition<std::logic_error>(backlog >= 0, "listen backlog cannot be negative");
    assert_socket(::listen(m_socket_fd, backlog) == 0, "listen");
}

ISocket::Ptr SocketPosix::accept() {
    assert_condition<std::logic_error>(is_open(), "Cannot accept on a closed socket");

    int fd;

    do {
        fd = ::accept(m_socket_fd, nullptr, nullptr);
    } while (fd < 0 && errno == EINTR);

    assert_socket(fd >= 0, "accept");
    return std::unique_ptr<ISocket>(new SocketPosix(fd));
}

std::size_t SocketPosix::send(std::string_view data) {
    assert_condition<std::logic_error>(is_open(), "Cannot send on a closed socket");

    if (data.empty()) {
        return 0;
    }

    constexpr auto max_count = std::numeric_limits<ssize_t>::max();
    const std::size_t count = data.size() > static_cast<std::size_t>(max_count)
                                  ? static_cast<std::size_t>(max_count)
                                  : data.size();
    ssize_t byte_count;

    do {
        byte_count = ::send(m_socket_fd, data.data(), count, SEND_FLAGS);
    } while (byte_count < 0 && errno == EINTR);

    assert_socket(byte_count >= 0, "send");
    return static_cast<std::size_t>(byte_count);
}

bool SocketPosix::receive(std::vector<std::byte>& buffer) {
    assert_condition<std::logic_error>(is_open(), "Cannot receive on a closed socket");

    if (buffer.empty()) {
        return false;
    }

    constexpr auto max_count = std::numeric_limits<ssize_t>::max();
    const auto count = buffer.size() > static_cast<std::size_t>(max_count)
                                  ? static_cast<std::size_t>(max_count)
                                  : buffer.size();

    ssize_t byte_count;

    do {
        byte_count = ::recv(m_socket_fd, buffer.data(), count, 0);
    } while (byte_count < 0 && errno == EINTR);

    assert_socket(byte_count >= 0, "recv");

    const auto received_size = static_cast<std::size_t>(byte_count);
    buffer.resize(received_size);

    // Returns true if bytes were read, false if EOF (0 bytes) was hit
    return received_size > 0;
}

Address SocketPosix::local_address() const {
    assert_condition<std::logic_error>(is_open(),
                                        "Cannot query the address of a closed socket");
    return query_socket_address(m_socket_fd, ::getsockname, "getsockname");
}

Address SocketPosix::remote_address() const {
    assert_condition<std::logic_error>(is_open(),
                                        "Cannot query the address of a closed socket");
    return query_socket_address(m_socket_fd, ::getpeername, "getpeername");
}

bool SocketPosix::is_open() const noexcept {
    return m_socket_fd >= 0;
}

void SocketPosix::set_receive_timeout(ISocket::Milliseconds timeout) {
    assert_condition<std::logic_error>(is_open(),
                                                "Cannot set timeout on a closed socket");
    assert_condition<std::invalid_argument>(timeout.count() >= 0,
                                                            "timeout cannot be negative");
    assert_socket(::set_timeout(m_socket_fd, DirectionReceive, timeout) == 0,
                  "setsockopt(SO_RCVTIMEO)");
}

void SocketPosix::set_send_timeout(ISocket::Milliseconds timeout) {
    assert_condition<std::logic_error>(is_open(),
                                                "Cannot set timeout on a closed socket");
    assert_condition<std::invalid_argument>(timeout.count() >= 0,
                                                            "timeout cannot be negative");
    assert_socket(::set_timeout(m_socket_fd, DirectionSend, timeout) == 0,
                  "setsockopt(SO_SNDTIMEO)");
}

void SocketPosix::close() {
    if (m_socket_fd < 0) {
        return; // calling close twice is safe
    }

    const auto fd = m_socket_fd;

    m_socket_fd = -1;
    // Do not retry close() after EINTR: on Linux the descriptor is released.

    assert_socket(::close(fd) == 0 || errno == EINTR, "close");
}

} // namespace net

#endif // __linux__ || __APPLE__
