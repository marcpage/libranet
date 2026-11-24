#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <filesystem>
#include <functional>
#include <future>
#include <list>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "msg/AsyncChannel.hpp"
#include "msg/IMessage.hpp"
#include "storage/DataStored.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"

namespace eviction {

// Keeps the node within its storage limits (High-Level Design §4.5), from a
// thread of its own: the content held takes no more than
// storage.max_storage_bytes, where that is set, and at least
// storage.min_free_bytes are left free where it is kept, unless that is 0.
// After each data.stored it checks both, aiming storage.max_hand_offs
// objects as large as one may be (1 MiB, §4.3) short of each, so that it is
// handing content off before a limit is reached.
//
// It asks stats::Recorder for the objects to let go of first, posting
// eviction.candidates_requested, and works through the list
// eviction.candidates answers with, asking for more once that runs out. It
// hands each off (peer::HandOff) on a thread of its own, at most
// storage.max_hand_offs at once, and only as many as would bring storage
// within its limits once they are deleted. That thread posts what came of
// it as eviction.handed_off, and the evictor's deletes the object once a
// peer took it, posting data.deleted. An object it finds the store no longer
// holds is posted as deleted too, so that it is not listed again.
//
// It never lets go of a key that signatures are checked against: this
// node's own, and those of the nodes connected to it either way. Nor of
// what was stored within the last storage.stored_grace_seconds, which a
// request waiting for it most likely has yet to read. They are left out of
// what it asks for, passed over in the list, and kept if a peer took them
// meanwhile.
//
// A hand-off no peer took holds off new ones until the next data.stored,
// as the peers connected may have changed by then. It asks for a list at
// most once for each data.stored and each object deleted, and while one is
// asked for, again only once storage.candidates_timeout_seconds have
// passed. So a list that is empty, or that names nothing it can hand off,
// waits for the next data.stored.
class Evictor {
public:
    using Clock = std::chrono::steady_clock;
    using Milliseconds = std::chrono::milliseconds;
    using ClockFunction = std::function<Clock::time_point()>;
    // The bytes free where content is kept, as far as this node may use
    // them.
    using FreeSpace = std::function<std::uintmax_t()>;

    // What it asks of the node's peers, from its threads, several at once:
    // to hand an object off, giving the node id of the peer that took it,
    // if one did, as peer::HandOff::hand_off() does; and the node ids of the
    // nodes connected to this node either way, as they are now.
    struct Peers {
        std::function<std::optional<storage::Identifier>(const storage::Identifier&)> hand_off;
        std::function<std::vector<storage::Identifier>()> connected;
    };

    // An object held, and how many bytes it takes as stored.
    struct Held {
        storage::Identifier identifier;
        std::size_t size = 0;
    };

    // NOLINTBEGIN(*-non-private-member-variables-in-classes)

    // A request for the objects held to let go of first, enough to free
    // bytes, leaving out this node's key and those exclude names, which
    // stats::Recorder answers with eviction.candidates.
    struct CandidatesRequested {
        static constexpr auto name = "eviction.candidates_requested";

        std::uintmax_t bytes = 0;
        std::vector<storage::Identifier> exclude;

        explicit operator std::string() const;
    };

    // The objects held to let go of first, the first first: enough to free
    // the bytes asked for, or as many as may be listed.
    struct Candidates {
        static constexpr auto name = "eviction.candidates";

        std::vector<Held> objects;

        explicit operator std::string() const;
    };

    // What came of a hand-off, which the thread that ran it posts for the
    // evictor's own to act on: the node id of the peer that took the
    // object, if one did.
    struct HandedOff {
        static constexpr auto name = "eviction.handed_off";

        Held held;
        std::optional<storage::Identifier> holder;

        explicit operator std::string() const;
    };

    // NOLINTEND(*-non-private-member-variables-in-classes)

    struct Settings {
        // As the Python node's are.
        static constexpr std::uintmax_t default_min_free_bytes = 1 * types::Gigabyte;
        static constexpr std::size_t default_max_hand_offs = 8;
        static constexpr Milliseconds default_stored_grace{30'000};
        static constexpr Milliseconds default_candidates_timeout{60'000};

        // storage.max_storage_bytes: the most the content held may take, as
        // stored; none for no limit.
        std::optional<std::uintmax_t> max_storage_bytes = std::nullopt;
        // storage.min_free_bytes: the fewest to leave free; 0 for no limit.
        std::uintmax_t min_free_bytes = default_min_free_bytes;
        // storage.max_hand_offs: the most hand-offs under way at once.
        std::size_t max_hand_offs = default_max_hand_offs;
        // storage.stored_grace_seconds
        Milliseconds stored_grace = default_stored_grace;
        // storage.candidates_timeout_seconds: how long a request for a list
        // goes unanswered before it may be asked again.
        Milliseconds candidates_timeout = default_candidates_timeout;
    };

    // The bytes free in directory's file system, as far as this node may
    // use them, read each time it is called. It throws
    // std::filesystem::filesystem_error when they cannot be read.
    [[nodiscard]] static FreeSpace free_space_in(std::filesystem::path directory);

    // Keeps what store holds within the limits settings give, reading the
    // bytes free with free_space, and lets go of nothing that is own_id's
    // key. Where the content held is limited, what store holds is counted
    // now. Throws std::invalid_argument, naming the setting, for no
    // hand-offs at once, a stored grace less than zero, or a candidates
    // timeout not more than zero; or for no store, or peers, free_space,
    // or clock with no function.
    Evictor(storage::Identifier own_id, storage::IDataSource::Ptr store, Peers peers,
            FreeSpace free_space, Settings settings, ClockFunction clock = &Clock::now);
    // Stops its thread, and waits for each hand-off under way, which ends
    // within the request timeouts of the peers it is offered to.
    ~Evictor();

    // Its thread holds this object, so it is neither copied nor moved.
    Evictor(const Evictor&) = delete;
    Evictor& operator=(const Evictor&) = delete;
    Evictor(Evictor&&) = delete;
    Evictor& operator=(Evictor&&) = delete;

private:
    using Identifiers = std::vector<storage::Identifier>;

    storage::Identifier m_own_id;
    storage::IDataSource::Ptr m_store;
    Peers m_peers;
    FreeSpace m_free_space;
    Settings m_settings;
    ClockFunction m_clock;

    // Used only by the thread, but for m_jobs, which the destructor takes
    // once the thread has stopped.
    //
    // Mistaken: the constructor sets m_held. cppcheck loses the class's
    // constructor in the files that name it with
    // `using eviction::Evictor;`.
    // cppcheck-suppress uninitMemberVarPrivate
    std::uintmax_t m_held;                             // Counted where limited
    std::map<std::string, Clock::time_point> m_stored; // Within the grace
    std::map<std::string, Held> m_handing_off;         // By identifier
    std::deque<Held> m_candidates;                     // The list, as left
    std::optional<Clock::time_point> m_asked_at;       // While asking
    bool m_may_ask = false;                            // Since the last ask
    bool m_held_off = false;                           // Until the next store
    std::list<std::future<void>> m_jobs;               // The hand-offs

    // Made last, and so stopped first: the thread, which uses all of the
    // above.
    msg::AsyncChannel m_channel;

    void _handle(const msg::IMessage::Ptr& message);
    void _stored(const storage::DataStored& stored);
    void _take(const Candidates& candidates);
    void _handed_off(const HandedOff& handed_off);
    void _evict();
    void _ask(std::uintmax_t bytes, const Identifiers& kept);
    void _hand_off(const Held& held);
    void _delete(const Held& held, const storage::Identifier& holder);
    void _forget(const Held& held);
    [[nodiscard]] bool _passes_over(const storage::Identifier& identifier,
                                    const Identifiers& kept) const;
    [[nodiscard]] Identifiers _kept() const;
    [[nodiscard]] std::uintmax_t _excess(std::uintmax_t headroom) const;
};

} // namespace eviction
