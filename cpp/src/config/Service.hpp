#pragma once

#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include "identity/NodeIdentity.hpp"
#include "net/Address.hpp"
#include "web/Server.hpp"

namespace config {

// /config's port (HTTP API §2.3): a web server at a loopback address alone,
// apart from the main port, serving nothing outside /config. Every handler is
// behind a Guard, and every answer is signed as the node, the guard's
// refusals among them. A GET of /config, or of a path beneath it outside
// /config/api, is the page. GET /config/api names each endpoint it serves,
// GET /config/api/node says what the node is, and NotOffered answers each
// path the shared page uses that a later phase brings. It serves from a
// thread of its own.
// cppcheck-suppress noConstructor
class Service {
public:
    // Where /config listens, and nowhere else.
    static constexpr std::string_view listen_host = "127.0.0.1";
    // With no port named, /config takes the first port free of this many
    // above the main port, twice as many, and so on, as the Python node does.
    static constexpr net::Address::Port port_step = 100;

    // Listens at listen_host on port, or, when none is named, on the first
    // port free of the main port + port_step, + 2 * port_step, and so on,
    // logging the port it took; port 0 has the system pick one. main_address
    // is where the main port listens, and own_endpoints those its node list
    // names the node at, as api::Service gives them. The credential is kept
    // in data_dir. identity must outlive this. Throws std::invalid_argument
    // when no port is named and the main port leaves none port_step above it,
    // or when own_endpoints is empty, and std::system_error when it cannot
    // listen on the port named, or on any it tries.
    Service(const identity::NodeIdentity& identity, const std::filesystem::path& data_dir,
            const net::Address& main_address, const std::vector<std::string>& own_endpoints,
            std::optional<net::Address::Port> port);
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
    web::Server m_server;
    // Mistaken: cppcheck loses the constructors in files `using` the class.
    // cppcheck-suppress uninitMemberVarNoCtor
    net::Address m_local_address; // Where m_server listens
    // Made last, and so stopped first.
    std::thread m_server_thread;
};

} // namespace config
