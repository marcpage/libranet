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
// thread of its own, handing content off to peers before deleting it. It never
// lets go of a key signatures are checked against, nor of what was stored
// within the stored grace.
class Evictor {
public:
    using Clock = std::chrono::steady_clock;
    using Milliseconds = std::chrono::milliseconds;
    using ClockFunction = std::function<Clock::time_point()>;
    // The bytes free where content is kept, as far as this node may use them.
    using FreeSpace = std::function<std::uintmax_t()>;

    // Called from its threads, several at once. hand_off() gives the node id
    // of the peer that took the object, if one did.
    struct Peers {
        std::function<std::optional<storage::Identifier>(const storage::Identifier&)> hand_off;
        std::function<std::vector<storage::Identifier>()> connected;
    };

    struct Held {
        storage::Identifier identifier;
        std::size_t size = 0;
    };

    // Events: plain structs, but for their conversion to text.
    // NOLINTBEGIN(*-non-private-member-variables-in-classes)

    struct CandidatesRequested {
        static constexpr auto name = "eviction.candidates_requested";

        std::uintmax_t bytes = 0;
        std::vector<storage::Identifier> exclude;

        explicit operator std::string() const;
    };

    struct Candidates {
        static constexpr auto name = "eviction.candidates";

        std::vector<Held> objects;

        explicit operator std::string() const;
    };

    struct HandedOff {
        static constexpr auto name = "eviction.handed_off";

        Held held;
        std::optional<storage::Identifier> holder;

        explicit operator std::string() const;
    };

    // NOLINTEND(*-non-private-member-variables-in-classes)

    struct Settings {
        // As the Python node's are.
        static constexpr std::uintmax_t default_min_free_bytes = 1024 * types::Megabyte;
        static constexpr std::size_t default_max_hand_offs = 8;
        static constexpr Milliseconds default_stored_grace{30'000};
        static constexpr Milliseconds default_candidates_timeout{60'000};

        // None for no limit.
        std::optional<std::uintmax_t> max_storage_bytes = std::nullopt;
        // 0 for no limit.
        std::uintmax_t min_free_bytes = default_min_free_bytes;
        std::size_t max_hand_offs = default_max_hand_offs;
        Milliseconds stored_grace = default_stored_grace;
        // How long a request for a list goes unanswered before it is asked
        // again.
        Milliseconds candidates_timeout = default_candidates_timeout;
    };

    // Read each time it is called. Throws std::filesystem::filesystem_error
    // when they cannot be read.
    [[nodiscard]] static FreeSpace free_space_in(std::filesystem::path directory);

    // Throws std::invalid_argument, naming the setting, for one it cannot use,
    // or for no store, or a function missing.
    Evictor(storage::Identifier own_id, storage::IDataSource::Ptr store, Peers peers,
            FreeSpace free_space, Settings settings, ClockFunction clock = &Clock::now);
    // Waits for each hand-off under way, which ends within the peers' request
    // timeouts.
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

    // Used only by the thread, but for m_jobs, which the destructor takes once
    // the thread has stopped.
    // Mistaken: cppcheck loses the constructor in files `using` the class.
    // cppcheck-suppress uninitMemberVarPrivate
    std::uintmax_t m_held;                             // Counted where limited
    std::map<std::string, Clock::time_point> m_stored; // Within the grace
    std::map<std::string, Held> m_handing_off;         // By identifier
    std::deque<Held> m_candidates;                     // The list, as left
    std::optional<Clock::time_point> m_asked_at;       // While asking
    bool m_may_ask = false;                            // Since the last ask
    bool m_held_off = false;                           // Until the next store
    std::list<std::future<void>> m_jobs;               // The hand-offs

    // Made last, and so stopped first.
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
