#pragma once

#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include "DialOutcomes.hpp"
#include "identity/NodeIdentity.hpp"
#include "net/Address.hpp"
#include "peer/PeerConnection.hpp"
#include "web/Server.hpp"

namespace config {

// /config's port (HTTP API §2.3): a web server at a loopback address, apart
// from the main port, serving only /config, signing every answer as the node.
// cppcheck-suppress noConstructor
class Service {
public:
    static constexpr std::string_view listen_host = "127.0.0.1";
    // With no port named, /config takes the first port free of the main port
    // + port_step, + 2 * port_step, and so on, as the Python node does.
    static constexpr net::Address::Port port_step = 100;

    // Port 0 has the system pick one. own_endpoints are those the node list
    // names the node at, hosts those /config is served as
    // (network.config_hosts), and limits those peer::Connections dials with.
    // identity must outlive this. Throws std::invalid_argument when no port is
    // named and none is left above the main port, for no own_endpoints, or for
    // a host holding a NUL, and std::system_error when it cannot listen.
    Service(const identity::NodeIdentity& identity, const std::filesystem::path& data_dir,
            const net::Address& main_address, const std::vector<std::string>& own_endpoints,
            std::optional<net::Address::Port> port, std::vector<std::string> hosts,
            const peer::PeerConnection::Limits& limits);
    // Ends every connection, and waits for its threads.
    ~Service();

    // With the port it took.
    [[nodiscard]] const net::Address& local_address() const noexcept;

    // Its threads hold this object, so it is neither copied nor moved.
    Service(const Service&) = delete;
    Service& operator=(const Service&) = delete;
    Service(Service&&) = delete;
    Service& operator=(Service&&) = delete;

private:
    // Made first, and so let go of last, once no handler waits on it.
    DialOutcomes m_dial_outcomes;
    web::Server m_server;
    // Mistaken: cppcheck loses the constructors in files `using` the class.
    // cppcheck-suppress uninitMemberVarNoCtor
    net::Address m_local_address; // Where m_server listens
    // Made last, and so stopped first.
    std::thread m_server_thread;
};

} // namespace config
