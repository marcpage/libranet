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

// The answers to searches for a prefix of a hash (HttpApi §6), best first,
// from what the node holds and what its peers named. An answer is kept until
// it has gone ttl unchanged. Safe to use from any thread.
// cppcheck-suppress noConstructor
class SearchAnswers {
public:
    using Clock = std::chrono::steady_clock;
    using Milliseconds = std::chrono::milliseconds;
    // In any order. Called from the threads searching, several at once.
    using Held = std::function<std::vector<Identifier>(const HashPrefix&)>;
    using ClockFunction = std::function<Clock::time_point()>;

    // So that what peers name cannot grow an answer without end. One sent
    // names fewer, as it is less than 1 MiB even compressed (HttpApi §6.1).
    static constexpr std::size_t max_identifiers = 65'536;
    // The min_bits a node answers with, as the Python node's
    // storage.search_min_bits is.
    static constexpr std::size_t default_min_bits = 24;

    // Throws std::invalid_argument for a ttl not more than zero, or a function
    // missing.
    SearchAnswers(Held held, std::size_t min_bits, Milliseconds ttl,
                  ClockFunction clock = &Clock::now);

    // The one kept, or else a new one, of what the node holds.
    [[nodiscard]] std::vector<Identifier> answer(const HashPrefix& prefix);

    // Less what does not match prefix, as a peer may hold its answers to fewer
    // bits.
    void add(const HashPrefix& prefix, const std::vector<Identifier>& named);

    ~SearchAnswers() = default;

    SearchAnswers(const SearchAnswers&) = delete;
    SearchAnswers& operator=(const SearchAnswers&) = delete;
    SearchAnswers(SearchAnswers&&) = delete;
    SearchAnswers& operator=(SearchAnswers&&) = delete;

private:
    struct Answer {
        std::vector<Identifier> identifiers; // Best first
        Clock::time_point changed;
    };

    Held m_held;
    // Mistaken: cppcheck loses the constructors in files `using` the class.
    // cppcheck-suppress uninitMemberVarNoCtor
    std::size_t m_min_bits;
    Milliseconds m_ttl;
    ClockFunction m_clock;
    std::mutex m_mutex;
    std::map<std::string, Answer> m_answers; // By prefix; guarded by m_mutex
    Clock::time_point m_swept;               // Guarded by m_mutex

    [[nodiscard]] std::optional<std::vector<Identifier>> _kept(const HashPrefix& prefix);
    [[nodiscard]] std::vector<Identifier> _merge(const HashPrefix& prefix,
                                                 std::vector<Identifier> found);
    void _sweep(Clock::time_point now);
};

} // namespace storage
