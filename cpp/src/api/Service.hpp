#pragma once

#include <chrono>
#include <cstddef>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "DataHandler.hpp"
#include "NodesHandler.hpp"
#include "SearchHandler.hpp"
#include "SeekHandler.hpp"
#include "SignatureGuard.hpp"
#include "identity/MessageVerifier.hpp"
#include "identity/NodeIdentity.hpp"
#include "msg/AsyncChannel.hpp"
#include "net/Address.hpp"
#include "protocol/NodeList.hpp"
#include "storage/IDataSource.hpp"
#include "storage/SearchAnswers.hpp"
#include "types/Constants.hpp"
#include "web/Server.hpp"

namespace api {

// The node's web server, and its handlers, each behind a SignatureGuard. It
// serves from a thread of its own, and keeps the lists stats::Recorder posts
// on another, so it is made before the recorder.
// cppcheck-suppress noConstructor
class Service {
public:
    using Milliseconds = std::chrono::milliseconds;

    // As the Python node's are.
    static constexpr std::size_t default_search_min_bits = 24;
    static constexpr Milliseconds default_search_cache_ttl{300'000};

    struct Settings {
        std::size_t search_min_bits = default_search_min_bits;    // storage.search_min_bits
        Milliseconds search_cache_ttl = default_search_cache_ttl; // storage.search_cache_ttl_seconds
        // How long a client asking for content the node will look for is told
        // to wait.
        DataHandler::Seconds retry_after = DataHandler::default_retry_after;
        // With no function, the node always looks for content it does not
        // hold.
        DataHandler::IsHeldOff is_held_off = nullptr;
        identity::MessageVerifier::Window window;
        std::size_t provisional_trust_attempts =
            SignatureGuard::default_provisional_trust_attempts;
        // The most a list sent compressed may decompress to.
        std::size_t list_bytes = types::max_decompressed_list_size;
        // The largest body a request may have, a PUT's content among them.
        std::size_t object_bytes = DataHandler::max_body_bytes;
        // The node list and seek list served are each sent in fewer bytes.
        std::size_t served_list_bytes = protocol::NodeList::max_size;
    };

    // identity must outlive this. Throws std::invalid_argument for a setting
    // it cannot use, and std::system_error when it cannot listen at address.
    Service(const net::Address& address, const storage::IDataSource::Ptr& store,
            const identity::NodeIdentity& identity, Settings settings);
    // Ends every connection, and waits for its threads.
    ~Service();

    // With the port the system picked when address left that to it.
    [[nodiscard]] const net::Address& local_address() const noexcept;

    // Those its node list names the node at until it is given another.
    [[nodiscard]] const std::vector<std::string>& own_endpoints() const noexcept;

    // Its threads hold this object, so it is neither copied nor moved.
    Service(const Service&) = delete;
    Service& operator=(const Service&) = delete;
    Service(Service&&) = delete;
    Service& operator=(Service&&) = delete;

private:
    DataHandler m_data_handler;
    SeekHandler m_seek_handler;
    std::shared_ptr<storage::SearchAnswers> m_answers; // What m_search_handler answers from
    SearchHandler m_search_handler;
    SignatureGuard m_guard; // In front of each of m_server's handlers
    web::Server m_server;
    // Mistaken: cppcheck loses the constructors in files `using` the class.
    // cppcheck-suppress uninitMemberVarNoCtor
    net::Address m_local_address; // Where m_server listens
    // Naming m_local_address's port, so made after it.
    std::vector<std::string> m_own_endpoints;
    NodesHandler m_nodes_handler;

    // Made last, and so stopped first.
    msg::AsyncChannel m_lists;
    std::thread m_server_thread;
};

} // namespace api
