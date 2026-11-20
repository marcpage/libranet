#pragma once

#include <chrono>
#include <cstddef>
#include <functional>
#include <map>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

#include "HashPrefix.hpp"
#include "Identifier.hpp"

namespace storage {

// The answers to searches for a prefix of a hash (HttpApi §6). A search is
// answered with the identifiers of what the node holds, and of what its
// peers named for the prefix, that share at least min_bits leading bits with
// it, or all of a shorter prefix: what shares fewer is near it, but was
// placed for something else. They come best first: those that share the
// most bits, then in order of their algorithm and hash, so that the same
// identifiers always rank alike.
//
// An answer is kept, and searches for its prefix answered from it, until it
// has gone ttl unchanged, so that content stored meanwhile may not be listed
// until then. Safe to use from any thread.
// cppcheck-suppress noConstructor
class SearchAnswers {
public:
    using Clock = std::chrono::steady_clock;
    using Milliseconds = std::chrono::milliseconds;
    // The identifiers of what the node holds whose hash begins with a
    // prefix, in any order. It is called from whichever threads search,
    // perhaps several at once.
    using Held = std::function<std::vector<Identifier>(const HashPrefix&)>;
    using Now = std::function<Clock::time_point()>;

    // The most identifiers an answer keeps, the best, so that what peers
    // name cannot grow one without end. An answer sent names fewer, as it is
    // less than 1 MiB even compressed (HttpApi §6.1): 1 MiB at 16 bytes a
    // hash, less than the 32 a SHA-256 digest is.
    static constexpr std::size_t max_identifiers = 65'536;

    // min_bits is storage.search_min_bits, and ttl
    // storage.search_cache_ttl_seconds. Throws std::invalid_argument for a
    // ttl that is not more than zero, or held or now with no function.
    SearchAnswers(Held held, std::size_t min_bits, Milliseconds ttl, Now now = &Clock::now);
    ~SearchAnswers() = default;
    // Its mutex is neither copied nor moved.
    SearchAnswers(const SearchAnswers&) = delete;
    SearchAnswers& operator=(const SearchAnswers&) = delete;
    SearchAnswers(SearchAnswers&&) = delete;
    SearchAnswers& operator=(SearchAnswers&&) = delete;

    // The answer to a search for prefix: the one kept, or else a new one,
    // of what the node holds.
    [[nodiscard]] std::vector<Identifier> answer(const HashPrefix& prefix);

    // Adds what a peer named for prefix to its answer, less what does not
    // match it, as a peer may hold its answers to fewer bits. When no answer
    // is kept, one is made first, of what the node holds.
    void add(const HashPrefix& prefix, const std::vector<Identifier>& named);

private:
    struct Answer {
        std::vector<Identifier> identifiers; // Best first
        Clock::time_point changed;
    };

    Held m_held;
    // Mistaken: the constructor sets m_min_bits. cppcheck loses the class's
    // constructors in a file that names it with `using
    // storage::SearchAnswers;`.
    // cppcheck-suppress uninitMemberVarNoCtor
    std::size_t m_min_bits;
    Milliseconds m_ttl;
    Now m_now;
    std::mutex m_mutex;
    std::map<std::string, Answer> m_answers; // By prefix; guarded by m_mutex
    Clock::time_point m_swept;               // Guarded by m_mutex

    [[nodiscard]] std::optional<std::vector<Identifier>> _kept(const HashPrefix& prefix);
    [[nodiscard]] std::vector<Identifier> _merge(const HashPrefix& prefix,
                                                 std::vector<Identifier> found);
    void _sweep(Clock::time_point now);
};

} // namespace storage
