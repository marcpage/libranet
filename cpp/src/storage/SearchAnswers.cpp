#include "SearchAnswers.hpp"

#include <algorithm>
#include <cstddef>
#include <iterator>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <utility>
#include <vector>

#include "HashPrefix.hpp"
#include "Identifier.hpp"

namespace {

// A hex digit is four bits.
constexpr std::size_t bits_per_digit = 4;

// The fewest leading bits a match for prefix shares with it: min_bits, or
// all of a shorter prefix (HttpApi §6).
std::size_t least_bits(const storage::HashPrefix& prefix, std::size_t min_bits) {
    return std::min(min_bits, prefix.bits());
}

// Whether an answer that last changed at changed is still kept at now.
bool is_fresh(storage::SearchAnswers::Clock::time_point changed,
              storage::SearchAnswers::Clock::time_point now,
              storage::SearchAnswers::Milliseconds ttl) {
    return now - changed < ttl;
}

// Whether left matches prefix better than right does: it shares more
// leading bits with it, or as many and comes first in order of algorithm
// and hash.
bool is_better(const storage::HashPrefix& prefix, const storage::Identifier& left,
               const storage::Identifier& right) {
    const auto left_bits = prefix.matching_bits(left);
    const auto right_bits = prefix.matching_bits(right);

    if (left_bits != right_bits) {
        return left_bits > right_bits;
    }

    return std::tie(left.algorithm(), left.hash()) < std::tie(right.algorithm(), right.hash());
}

// Whether left and right name the same content.
bool is_same(const storage::Identifier& left, const storage::Identifier& right) {
    return left.algorithm() == right.algorithm() && left.hash() == right.hash();
}

// identifiers, the best match for prefix first, each once, and no more of
// them than an answer keeps.
std::vector<storage::Identifier> ranked(const storage::HashPrefix& prefix,
                                        std::vector<storage::Identifier> identifiers) {
    std::sort(identifiers.begin(), identifiers.end(),
              [&prefix](const auto& left, const auto& right) {
        return is_better(prefix, left, right);
    });
    identifiers.erase(std::unique(identifiers.begin(), identifiers.end(), is_same),
                      identifiers.end());

    if (identifiers.size() > storage::SearchAnswers::max_identifiers) {
        identifiers.resize(storage::SearchAnswers::max_identifiers);
    }

    return identifiers;
}

// identifiers, less those that share fewer than least leading bits with
// prefix.
std::vector<storage::Identifier> matching(const storage::HashPrefix& prefix,
                                          std::vector<storage::Identifier> identifiers,
                                          std::size_t least) {
    const auto too_few_bits = [&prefix, least](const storage::Identifier& identifier) {
        return prefix.matching_bits(identifier) < least;
    };

    identifiers.erase(std::remove_if(identifiers.begin(), identifiers.end(), too_few_bits),
                      identifiers.end());
    return identifiers;
}

// The prefixes that each hash sharing least leading bits with prefix begins
// with one of: as many of prefix's digits as those bits fill, or, when they
// fill none, each digit that shares them.
std::vector<storage::HashPrefix> scanned(const storage::HashPrefix& prefix, std::size_t least) {
    constexpr std::string_view hex_digits = "0123456789abcdef";
    const auto digits = least / bits_per_digit;
    std::vector<storage::HashPrefix> prefixes;

    if (digits > 0) {
        prefixes.push_back(storage::HashPrefix::parse(prefix.to_string().substr(0, digits)));
        return prefixes;
    }

    for (const auto digit : hex_digits) {
        auto first = storage::HashPrefix::parse(std::string(1, digit));

        if (first.matching_bits(prefix) >= least) {
            prefixes.push_back(std::move(first));
        }
    }

    return prefixes;
}

// What the node holds, as held lists it, that shares least leading bits
// with prefix.
std::vector<storage::Identifier> held_matching(const storage::SearchAnswers::Held& held,
                                               const storage::HashPrefix& prefix,
                                               std::size_t least) {
    std::vector<storage::Identifier> found;

    for (const auto& scanned_prefix : scanned(prefix, least)) {
        auto listed = held(scanned_prefix);

        found.insert(found.end(), std::make_move_iterator(listed.begin()),
                     std::make_move_iterator(listed.end()));
    }

    return matching(prefix, std::move(found), least);
}

} // namespace

namespace storage {

SearchAnswers::SearchAnswers(Held held, std::size_t min_bits, Milliseconds ttl, Now now)
    : m_held(std::move(held))
    , m_min_bits(min_bits)
    , m_ttl(ttl)
    , m_now(std::move(now)) {
    if (!m_held || !m_now) {
        throw std::invalid_argument("Searches need what the node holds, and the time");
    }

    if (m_ttl <= Milliseconds::zero()) {
        throw std::invalid_argument("An answer must be kept for more than no time");
    }
}

std::vector<Identifier> SearchAnswers::answer(const HashPrefix& prefix) {
    auto kept = _kept(prefix);

    if (kept) {
        return std::move(*kept);
    }

    return _merge(prefix, held_matching(m_held, prefix, least_bits(prefix, m_min_bits)));
}

void SearchAnswers::add(const HashPrefix& prefix, const std::vector<Identifier>& named) {
    // Made first, when none is kept.
    (void)answer(prefix);
    (void)_merge(prefix, matching(prefix, named, least_bits(prefix, m_min_bits)));
}

// The answer kept for prefix, if it is still fresh.
std::optional<std::vector<Identifier>> SearchAnswers::_kept(const HashPrefix& prefix) {
    const auto now = m_now();
    const std::scoped_lock lock(m_mutex);
    const auto kept = m_answers.find(prefix.to_string());

    if (kept == m_answers.end() || !is_fresh(kept->second.changed, now, m_ttl)) {
        return std::nullopt;
    }

    return kept->second.identifiers;
}

// Adds found, which all match prefix, to the answer kept for prefix, or
// keeps them as its answer when none is fresh, and gives the answer. It
// changes, and is kept from now, only when it names more or better.
std::vector<Identifier> SearchAnswers::_merge(const HashPrefix& prefix,
                                              std::vector<Identifier> found) {
    const auto now = m_now();
    const std::scoped_lock lock(m_mutex);

    _sweep(now);

    const auto& key = prefix.to_string();
    const auto kept = m_answers.find(key);
    const bool is_kept = kept != m_answers.end() && is_fresh(kept->second.changed, now, m_ttl);

    if (is_kept) {
        found.insert(found.end(), kept->second.identifiers.begin(),
                     kept->second.identifiers.end());
    }

    auto merged = ranked(prefix, std::move(found));

    if (!is_kept
        || !std::equal(merged.begin(), merged.end(), kept->second.identifiers.begin(),
                       kept->second.identifiers.end(), is_same)) {
        m_answers.insert_or_assign(key, Answer{merged, now});
    }

    return merged;
}

// Lets go of the answers no longer fresh, at most once each ttl, so that
// none is held more than twice as long as it is kept. The caller holds
// m_mutex.
void SearchAnswers::_sweep(Clock::time_point now) {
    if (now - m_swept < m_ttl) {
        return;
    }

    for (auto kept = m_answers.begin(); kept != m_answers.end();) {
        if (is_fresh(kept->second.changed, now, m_ttl)) {
            ++kept;
        } else {
            kept = m_answers.erase(kept);
        }
    }

    m_swept = now;
}

} // namespace storage
