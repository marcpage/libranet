#pragma once

#include <chrono>
#include <cstddef>
#include <memory>
#include <string>
#include <string_view>
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
#include "msg/IMessage.hpp"
#include "net/Address.hpp"
#include "storage/IDataSource.hpp"
#include "storage/SearchAnswers.hpp"
#include "web/Server.hpp"

namespace api {

// The node's web server, and what it serves: the content the node holds,
// beneath /data (DataHandler); its node list, /data/nodes (NodesHandler);
// its seek list, /data/seek (SeekHandler); and searches, /data/search
// (SearchHandler). A SignatureGuard stands in front of each, checking the
// signature on each request and signing each answer as the node. It serves
// from a thread of its own, started as it is made, and the threads
// web::Server starts for each connection.
//
// From another thread, it keeps the lists stats::Recorder derives, for the
// handlers to serve, as each is posted: the node list, from each
// nodes.updated, and the seek list, from each seek.updated. Until the first
// of each, the node list names the node alone, at http://localhost:{port},
// the port it listens on, as a node with no external address or port names
// itself (HttpApi §10.1), and the seek list names nothing. A list posted
// before this is made is not seen, so it is made before the recorder. On
// the same thread, what a peer names for a search, as it answers one this
// node asked it or names what it holds that the seek list's search matches
// (storage::SearchAnswered), is added to the answer kept for the prefix, so
// that a later search lists it (HttpApi §6, §10.7.2).
// cppcheck-suppress noConstructor
class Service {
public:
    using Milliseconds = std::chrono::milliseconds;

    // What storage.search_min_bits and storage.search_cache_ttl_seconds are
    // when not set, as the Python node's are.
    static constexpr std::size_t default_search_min_bits = 24;
    static constexpr Milliseconds default_search_cache_ttl{300'000};

    struct Settings {
        std::size_t search_min_bits = default_search_min_bits;    // storage.search_min_bits
        Milliseconds search_cache_ttl = default_search_cache_ttl; // storage.search_cache_ttl_seconds
        // network.retry_after_seconds: how long a client asking for content
        // the node will look for is told to wait.
        DataHandler::Seconds retry_after = DataHandler::default_retry_after;
        // Whether the node looks for content it does not hold, as
        // peer::Fetch::is_held_off() says; with no function, it always does.
        DataHandler::IsHeldOff is_held_off = nullptr;
        // identity.signature_max_age_seconds and
        // identity.signature_clock_skew_seconds.
        identity::MessageVerifier::Window window;
        // identity.provisional_trust_attempts
        std::size_t provisional_trust_attempts =
            SignatureGuard::default_provisional_trust_attempts;
    };

    // Listens at address, and serves what store holds, as the node identity
    // names, which must outlive this. Throws std::invalid_argument for a
    // search cache ttl that is not more than zero, or for what DataHandler,
    // MessageVerifier, or SignatureGuard refuse; and std::system_error when
    // it cannot listen at address.
    Service(const net::Address& address, const storage::IDataSource::Ptr& store,
            const identity::NodeIdentity& identity, Settings settings);
    // Stops serving: ends every connection, and waits for the threads
    // serving them and the lists.
    ~Service();

    // The address it listens on, with the port the system picked when the
    // address it was given left that to it.
    [[nodiscard]] const net::Address& local_address() const noexcept;

    // The endpoints its node list names the node at until it is given
    // another, which the node's other parts name it at too.
    [[nodiscard]] const std::vector<std::string>& own_endpoints() const noexcept;

    // Its threads hold this object, so it is neither copied nor moved.
    Service(const Service&) = delete;
    Service& operator=(const Service&) = delete;
    Service(Service&&) = delete;
    Service& operator=(Service&&) = delete;

private:
    // The connections the system queues until the server takes them.
    static constexpr int backlog = 128;

    DataHandler m_data_handler;
    SeekHandler m_seek_handler;
    std::shared_ptr<storage::SearchAnswers> m_answers; // What m_search_handler answers from
    SearchHandler m_search_handler;
    SignatureGuard m_guard; // In front of each of m_server's handlers
    web::Server m_server;
    // Mistaken: the constructor sets m_local_address. cppcheck loses the
    // class's constructors in a file that names it with `using api::Service;`.
    // cppcheck-suppress uninitMemberVarNoCtor
    net::Address m_local_address; // Where m_server listens
    // Naming m_local_address's port as the node's own, so made after it.
    std::vector<std::string> m_own_endpoints;
    NodesHandler m_nodes_handler;

    // Made last, and so stopped first: the thread keeping the lists and
    // what peers name, then the one serving, both of which use all of the
    // above.
    msg::AsyncChannel m_lists;
    std::thread m_server_thread;

    void _register(std::string_view path, const web::Server::RequestHandler& handler);
    void _keep(const msg::IMessage::Ptr& message);
    void _serve();
};

} // namespace api
