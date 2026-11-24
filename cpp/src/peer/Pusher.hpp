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

// Passes new content on toward the node whose id best matches its hash
// (HTTP API §7.4, High-Level Design §4.10). For each storage::DataStored,
// which is posted only for content the node did not hold before, it puts
// the content, as stored, to its best outgoing connection: the peer whose
// node id shares the most leading bits with the content's hash, even where
// this node's own id shares more, since that peer may be connected to a
// better match still. It puts nothing where that peer is the node the
// content came from, which ends the chain.
//
// Content stored while it pushes waits, and is then taken together, a run's
// length at most, so that what of it is bound for one peer goes there in one
// run of requests, sent without waiting for each answer. Each peer's runs go
// on a thread of their own, so that a peer slow to answer holds up none of
// the others. A peer that does not take what it is put is not passed over
// for the next best, and content that has no peer to go to is not kept to
// push later.
// cppcheck-suppress noConstructor
class Pusher {
public:
    using Session = std::shared_ptr<PeerSession>;
    // The sessions with the peers this node has an outgoing connection to,
    // as they are now. Called from its own thread, and from those calling
    // push().
    using GetPeers = std::function<std::vector<Session>()>;
    using Stored = std::vector<storage::DataStored>;

    // As the Python node's peers.pipeline_depth.
    static constexpr std::size_t default_run_length = 8;

    // Puts what is stored in store to the peers get_peers gives, at most
    // run_length, peers.pipeline_depth, in a run. Throws
    // std::invalid_argument for no store, get_peers with no function, or a run
    // length of zero.
    Pusher(storage::IDataSource::Ptr store, GetPeers get_peers,
           std::size_t run_length = default_run_length);
    // Stops its thread, once what it is pushing has been answered, which is
    // within the request timeout.
    ~Pusher() = default;
    // Its thread holds this object, so it is neither copied nor moved.
    Pusher(const Pusher&) = delete;
    Pusher& operator=(const Pusher&) = delete;
    Pusher(Pusher&&) = delete;
    Pusher& operator=(Pusher&&) = delete;

    // Puts each of stored that is still held to the peer it is bound for,
    // if any, as above, and waits for every answer. Gives how many a peer
    // accepted. A run that fails, as when the session ends, is logged, and
    // ends the pushes to that peer; content a peer refuses is logged too.
    std::size_t push(const Stored& stored) const;

private:
    storage::IDataSource::Ptr m_store;
    GetPeers m_get_peers;
    // Mistaken: the constructor sets m_run_length. cppcheck loses the
    // class's constructors in a file that names it with `using peer::Pusher;`.
    // cppcheck-suppress uninitMemberVarNoCtor
    std::size_t m_run_length;

    // Made last, and so stopped first, as its thread uses all of the above.
    msg::AsyncChannel m_channel;

    void _handle(const msg::IMessage::Ptr& message);
    [[nodiscard]] Stored _take_waiting(const msg::IMessage::Ptr& first);
};

} // namespace peer
