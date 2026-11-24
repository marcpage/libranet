#pragma once

#include <cstddef>
#include <functional>
#include <memory>
#include <vector>

#include "PeerSession.hpp"
#include "msg/AsyncChannel.hpp"
#include "msg/IMessage.hpp"
#include "storage/DataStored.hpp"
#include "storage/IDataSource.hpp"

namespace peer {

// Passes new content on toward the node whose id best matches its hash (HTTP
// API §7.4, High-Level Design §4.10), putting it to the best outgoing
// connection, unless that is the node it came from. What is stored while it
// pushes is taken together, in runs sent without waiting for each answer.
// cppcheck-suppress noConstructor
class Pusher {
public:
    using Session = std::shared_ptr<PeerSession>;
    // Called from its own thread, and from those calling push().
    using GetPeers = std::function<std::vector<Session>()>;
    using Stored = std::vector<storage::DataStored>;

    // As the Python node's peers.pipeline_depth.
    static constexpr std::size_t default_run_length = 8;

    // run_length is peers.pipeline_depth. Throws std::invalid_argument for no
    // store, get_peers with no function, or a run length of zero.
    Pusher(storage::IDataSource::Ptr store, GetPeers get_peers,
           std::size_t run_length = default_run_length);

    // Waits for every answer, and gives how many a peer accepted. A failure is
    // logged, and a run that fails ends the pushes to that peer.
    std::size_t push(const Stored& stored) const;

    // Once what it is pushing has been answered, within the request timeout.
    ~Pusher() = default;

    // Its thread holds this object, so it is neither copied nor moved.
    Pusher(const Pusher&) = delete;
    Pusher& operator=(const Pusher&) = delete;
    Pusher(Pusher&&) = delete;
    Pusher& operator=(Pusher&&) = delete;

private:
    storage::IDataSource::Ptr m_store;
    GetPeers m_get_peers;
    // Mistaken: cppcheck loses the constructors in files `using` the class.
    // cppcheck-suppress uninitMemberVarNoCtor
    std::size_t m_run_length;

    // Made last, and so stopped first.
    msg::AsyncChannel m_channel;

    void _handle(const msg::IMessage::Ptr& message);
    [[nodiscard]] Stored _take_waiting(const msg::IMessage::Ptr& first);
};

} // namespace peer
