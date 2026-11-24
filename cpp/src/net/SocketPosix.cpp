#if defined(__linux__) || defined(__APPLE__)

#include "SocketPosix.hpp"

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <limits>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <system_error>
#include <vector>

#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/types.h>
#include <unistd.h>

#include "Address.hpp"
#include "ISocket.hpp"

namespace {

#if __APPLE__
constexpr int send_flags = 0;
#elif defined(__linux__)
constexpr int send_flags = MSG_NOSIGNAL;
#endif

// The most bytes one send() or recv() is asked for: what it returns, a
// ssize_t, can count no more.
constexpr auto max_count = std::numeric_limits<ssize_t>::max();

void assert_socket(bool condition, const char* operation, int error = errno) {
    if (!condition) {
        throw std::system_error(error, std::generic_category(), operation);
    }
}

template<typename Error>
void assert_condition(bool condition, const std::string& message) {
    if (!condition) {
        throw Error(message);
    }
}

struct AddrInfoDeleter {
    void operator()(addrinfo* value) const noexcept {
        if (value != nullptr) {
            ::freeaddrinfo(value);
        }
    }
};

using AddrInfoPtr = std::unique_ptr<addrinfo, AddrInfoDeleter>;

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
        0 == error, std::string("getaddrinfo: ") + ::gai_strerror(error));

    return AddrInfoPtr(result);
}

int create_socket(const addrinfo& info) {
    const auto socket_fd = ::socket(info.ai_family, info.ai_socktype, info.ai_protocol);

    if (socket_fd < 0) {
        return -1;
    }

    const int enabled = 1;

    if (SOCK_STREAM == info.ai_socktype) {
        // Allow quick restart of a listening server after it has closed
        (void)::setsockopt(socket_fd, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));

        // Disable Nagle's algorithm (common for request/response and
        // low-latency use)
        (void)::setsockopt(socket_fd, IPPROTO_TCP, TCP_NODELAY, &enabled, sizeof(enabled));

        // Detect dead peers on long-lived connections
        (void)::setsockopt(socket_fd, SOL_SOCKET, SO_KEEPALIVE, &enabled, sizeof(enabled));
    }

#if __APPLE__
    // Prevent SIGPIPE when writing to a closed peer (macOS / BSD)
    (void)::setsockopt(socket_fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled));
#endif

    return socket_fd;
}

net::Address address_from_sockaddr(const sockaddr* address, socklen_t length) {
    std::array<char, NI_MAXHOST> host{};
    std::array<char, NI_MAXSERV> service{};

    const auto error = ::getnameinfo(address, length,
                                     host.data(), static_cast<socklen_t>(host.size()),
                                     service.data(), static_cast<socklen_t>(service.size()),
                                     NI_NUMERICHOST | NI_NUMERICSERV);

    assert_condition<std::runtime_error>(
        0 == error, std::string("getnameinfo: ") + ::gai_strerror(error));

    const auto port = std::stoul(service.data());

    assert_condition<std::runtime_error>(
        port <= std::numeric_limits<net::Address::Port>::max(),
        "Socket returned an invalid port");

    return {host.data(), static_cast<net::Address::Port>(port)};
}

using Clock = std::chrono::steady_clock;

// How long poll() is to wait, in milliseconds, until deadline: -1, as long
// as it takes, where there is none.
int poll_wait(const std::optional<Clock::time_point>& deadline) {
    if (!deadline) {
        return -1;
    }

    const auto left = std::chrono::ceil<std::chrono::milliseconds>(*deadline - Clock::now());

    return static_cast<int>(std::clamp<std::chrono::milliseconds::rep>(
        left.count(), 0, std::numeric_limits<int>::max()));
}

// Waits for the connect() under way on socket_fd to end, for at most
// timeout, or as long as it takes for zero. Returns 0 once it has
// connected, or the error it failed with: ETIMEDOUT once timeout passed.
int wait_connected(int socket_fd, net::ISocket::Milliseconds timeout) {
    // No longer than poll() can wait at once, about 24 days, so that the
    // deadline cannot overflow the clock.
    constexpr net::ISocket::Milliseconds longest{std::numeric_limits<int>::max()};
    std::optional<Clock::time_point> deadline;
    pollfd waiting{socket_fd, POLLOUT, 0};
    int error = 0;
    socklen_t length = sizeof(error);

    if (timeout.count() > 0) {
        deadline = Clock::now() + std::min(timeout, longest);
    }

    auto ready = ::poll(&waiting, 1, poll_wait(deadline));

    // A signal ends poll() early, and it waits again for what is left.
    while (ready < 0 && EINTR == errno) {
        ready = ::poll(&waiting, 1, poll_wait(deadline));
    }

    if (ready < 0) {
        return errno;
    }

    if (0 == ready) {
        return ETIMEDOUT;
    }

    if (0 != ::getsockopt(socket_fd, SOL_SOCKET, SO_ERROR, &error, &length)) {
        return errno;
    }

    return error;
}

// Connects socket_fd to the address info names, waiting at most timeout,
// or as long as it takes for zero. Returns 0 once it has connected, or the
// error it failed with. The socket does not block while it connects, so
// that it can stop waiting, and blocks again once connected.
int connect_within(int socket_fd, const addrinfo& info, net::ISocket::Milliseconds timeout) {
    // fcntl() is the POSIX call that sets a descriptor's flags, an int, and
    // it takes variable arguments; there is no other form.
    // NOLINTBEGIN(cppcoreguidelines-pro-type-vararg,hicpp-vararg,*-signed-bitwise)
    const auto flags = ::fcntl(socket_fd, F_GETFL);

    if (flags < 0 || ::fcntl(socket_fd, F_SETFL, flags | O_NONBLOCK) < 0) {
        return errno;
    }

    auto error = 0 == ::connect(socket_fd, info.ai_addr, info.ai_addrlen) ? 0 : errno;

    // The connection goes on being made, a signal or not.
    if (EINPROGRESS == error || EINTR == error) {
        error = wait_connected(socket_fd, timeout);
    }

    if (0 == error && ::fcntl(socket_fd, F_SETFL, flags) < 0) {
        error = errno;
    }
    // NOLINTEND(cppcoreguidelines-pro-type-vararg,hicpp-vararg,*-signed-bitwise)

    return error;
}

enum class Direction : std::uint8_t {
    Send,
    Receive,
};

int set_timeout(int socket_fd, Direction direction, net::ISocket::Milliseconds timeout) {
    const auto direction_flag = Direction::Send == direction ? SO_SNDTIMEO : SO_RCVTIMEO;
    timeval timeout_value{};

    if (timeout.count() > 0) {
        constexpr auto milliseconds_per_second = 1000;
        constexpr auto microseconds_per_millisecond = 1000;

        timeout_value.tv_sec = static_cast<time_t>(timeout.count() / milliseconds_per_second);
        timeout_value.tv_usec = static_cast<suseconds_t>(
            (timeout.count() % milliseconds_per_second) * microseconds_per_millisecond);
    }

    return ::setsockopt(socket_fd, SOL_SOCKET, direction_flag, &timeout_value,
                        sizeof(timeout_value));
}

template<typename Function>
net::Address query_socket_address(int socket_fd, Function socket_call,
                                  const char* operation_name) {
    sockaddr_storage storage{};
    socklen_t length = sizeof(storage);

    // The POSIX socket calls take a sockaddr*, and a sockaddr_storage is
    // made to be passed as one.
    // NOLINTBEGIN(cppcoreguidelines-pro-type-reinterpret-cast)
    const auto result = socket_call(socket_fd,
                                    reinterpret_cast<sockaddr*>(&storage), &length);

    assert_socket(0 == result, operation_name);
    return address_from_sockaddr(reinterpret_cast<const sockaddr*>(&storage), length);
    // NOLINTEND(cppcoreguidelines-pro-type-reinterpret-cast)
}

} // namespace

namespace net {

// cppcheck-suppress duplInheritedMember
ISocket::Ptr SocketPosix::bind(const Address& address) {
    const auto addresses = resolve_address(address, AI_PASSIVE, SOCK_STREAM);
    int last_error = EADDRNOTAVAIL;

    for (const auto* current = addresses.get();
         current != nullptr;
         current = current->ai_next) {
        const auto socket_fd = create_socket(*current);

        if (socket_fd < 0) {
            last_error = errno;
            continue;
        }

        if (0 == ::bind(socket_fd, current->ai_addr, current->ai_addrlen)) {
            return std::unique_ptr<SocketPosix>(new SocketPosix(socket_fd));
        }

        last_error = errno;
        (void)::close(socket_fd);
    }

    assert_socket(false, "bind", last_error);
    return nullptr;
}

// cppcheck-suppress duplInheritedMember
ISocket::Ptr SocketPosix::connect(const Address& address, ISocket::Milliseconds timeout) {
    const auto addresses = resolve_address(address, 0, SOCK_STREAM);
    int last_error = ECONNREFUSED;

    for (const addrinfo* current = addresses.get();
         current != nullptr;
         current = current->ai_next) {
        const auto socket_fd = create_socket(*current);

        if (socket_fd < 0) {
            last_error = errno;
            continue;
        }

        last_error = connect_within(socket_fd, *current, timeout);

        if (0 == last_error) {
            return std::unique_ptr<SocketPosix>(new SocketPosix(socket_fd));
        }

        (void)::close(socket_fd);
    }

    assert_socket(false, "connect", last_error);
    return nullptr;
}

SocketPosix::SocketPosix(int socket_fd) noexcept
    : m_socket_fd(socket_fd) {}

SocketPosix::~SocketPosix() {
    // close() throws when ::close fails, which a destructor cannot report.
    // The descriptor is released either way.
    if (m_socket_fd >= 0) {
        (void)::close(m_socket_fd);
    }
}

void SocketPosix::listen(int backlog) {
    assert_condition<std::logic_error>(is_open(), "Cannot listen on a closed socket");
    assert_condition<std::logic_error>(backlog >= 0, "listen backlog cannot be negative");
    assert_socket(0 == ::listen(m_socket_fd, backlog), "listen");
}

ISocket::Ptr SocketPosix::accept() {
    assert_condition<std::logic_error>(is_open(), "Cannot accept on a closed socket");

    int accepted_fd = -1;

    // The accepted socket is closed on exec, so it does not leak into child
    // processes.
    do {
#if __APPLE__
        // macOS has no accept4(), so close-on-exec is set once accepted.
        // NOLINTNEXTLINE(android-cloexec-accept)
        accepted_fd = ::accept(m_socket_fd, nullptr, nullptr);
#elif defined(__linux__)
        accepted_fd = ::accept4(m_socket_fd, nullptr, nullptr, SOCK_CLOEXEC);
#endif
    } while (accepted_fd < 0 && EINTR == errno);

    assert_socket(accepted_fd >= 0, "accept");

#if __APPLE__
    // fcntl() is the POSIX call that sets close-on-exec, and it takes
    // variable arguments; there is no other form.
    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-vararg)
    (void)::fcntl(accepted_fd, F_SETFD, FD_CLOEXEC);
#endif

    return std::unique_ptr<ISocket>(new SocketPosix(accepted_fd));
}

std::size_t SocketPosix::send(std::string_view data) {
    assert_condition<std::logic_error>(is_open(), "Cannot send on a closed socket");

    if (data.empty()) {
        return 0;
    }

    const std::size_t count = data.size() > static_cast<std::size_t>(max_count)
                                  ? static_cast<std::size_t>(max_count)
                                  : data.size();
    ssize_t byte_count = -1;

    do {
        byte_count = ::send(m_socket_fd, data.data(), count, send_flags);
    } while (byte_count < 0 && EINTR == errno);

    assert_socket(byte_count >= 0, "send");
    return static_cast<std::size_t>(byte_count);
}

bool SocketPosix::receive(std::vector<std::byte>& buffer) {
    assert_condition<std::logic_error>(is_open(), "Cannot receive on a closed socket");

    if (buffer.empty()) {
        return false;
    }

    const auto count = buffer.size() > static_cast<std::size_t>(max_count)
                           ? static_cast<std::size_t>(max_count)
                           : buffer.size();

    ssize_t byte_count = -1;

    do {
        byte_count = ::recv(m_socket_fd, buffer.data(), count, 0);
    } while (byte_count < 0 && EINTR == errno);

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
    assert_socket(0 == ::set_timeout(m_socket_fd, Direction::Receive, timeout),
                  "setsockopt(SO_RCVTIMEO)");
}

void SocketPosix::set_send_timeout(ISocket::Milliseconds timeout) {
    assert_condition<std::logic_error>(is_open(),
                                       "Cannot set timeout on a closed socket");
    assert_condition<std::invalid_argument>(timeout.count() >= 0,
                                            "timeout cannot be negative");
    assert_socket(0 == ::set_timeout(m_socket_fd, Direction::Send, timeout),
                  "setsockopt(SO_SNDTIMEO)");
}

void SocketPosix::shutdown() {
    assert_condition<std::logic_error>(is_open(), "Cannot shut down a closed socket");
    // ENOTCONN: the peer has gone already, which ends the connection too.
    assert_socket(0 == ::shutdown(m_socket_fd, SHUT_RDWR) || ENOTCONN == errno, "shutdown");
}

void SocketPosix::close() {
    if (m_socket_fd < 0) {
        return; // calling close twice is safe
    }

    const auto socket_fd = m_socket_fd;

    m_socket_fd = -1;
    // Do not retry close() after EINTR: on Linux the descriptor is released.

    assert_socket(0 == ::close(socket_fd) || EINTR == errno, "close");
}

} // namespace net

#endif // __linux__ || __APPLE__
