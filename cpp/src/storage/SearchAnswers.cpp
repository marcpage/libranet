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

using std::equal;
using std::invalid_argument;
using std::make_move_iterator;
using std::min;
using std::nullopt;
using std::optional;
using std::remove_if;
using std::scoped_lock;
using std::size_t;
using std::sort;
using std::string;
using std::string_view;
using std::tie;
using std::unique;
using std::vector;
using storage::HashPrefix;
using storage::Identifier;
using storage::SearchAnswers;

namespace {

// A hex digit is four bits.
constexpr size_t bits_per_digit = 4;

// The fewest leading bits a match for prefix shares with it: min_bits, or
// all of a shorter prefix (HttpApi §6).
size_t least_bits(const HashPrefix& prefix, size_t min_bits) {
    return min(min_bits, prefix.bits());
}

// Whether an answer that last changed at changed is still kept at now.
bool is_fresh(SearchAnswers::Clock::time_point changed,
              SearchAnswers::Clock::time_point now,
              SearchAnswers::Milliseconds ttl) {
    return now - changed < ttl;
}

// Whether left matches prefix better than right does: it shares more
// leading bits with it, or as many and comes first in order of algorithm
// and hash.
bool is_better(const HashPrefix& prefix, const Identifier& left,
               const Identifier& right) {
    const auto left_bits = prefix.matching_bits(left);
    const auto right_bits = prefix.matching_bits(right);

    if (left_bits != right_bits) {
        return left_bits > right_bits;
    }

    return tie(left.algorithm(), left.hash()) < tie(right.algorithm(), right.hash());
}

// Whether left and right name the same content.
bool is_same(const Identifier& left, const Identifier& right) {
    return left.algorithm() == right.algorithm() && left.hash() == right.hash();
}

// identifiers, the best match for prefix first, each once, and no more of
// them than an answer keeps.
vector<Identifier> ranked(const HashPrefix& prefix,
                          vector<Identifier> identifiers) {
    sort(identifiers.begin(), identifiers.end(),
         [&prefix](const auto& left, const auto& right) {
        return is_better(prefix, left, right);
    });
    identifiers.erase(unique(identifiers.begin(), identifiers.end(), is_same),
                      identifiers.end());

    if (identifiers.size() > SearchAnswers::max_identifiers) {
        identifiers.resize(SearchAnswers::max_identifiers);
    }

    return identifiers;
}

// identifiers, less those that share fewer than least leading bits with
// prefix.
vector<Identifier> matching(const HashPrefix& prefix,
                            vector<Identifier> identifiers,
                            size_t least) {
    const auto too_few_bits = [&prefix, least](const Identifier& identifier) {
        return prefix.matching_bits(identifier) < least;
    };

    identifiers.erase(remove_if(identifiers.begin(), identifiers.end(), too_few_bits),
                      identifiers.end());
    return identifiers;
}

// The prefixes that each hash sharing least leading bits with prefix begins
// with one of: as many of prefix's digits as those bits fill, or, when they
// fill none, each digit that shares them.
vector<HashPrefix> scanned(const HashPrefix& prefix, size_t least) {
    constexpr string_view hex_digits = "0123456789abcdef";
    const auto digits = least / bits_per_digit;
    vector<HashPrefix> prefixes;

    if (digits > 0) {
        prefixes.push_back(HashPrefix::parse(prefix.to_string().substr(0, digits)));
        return prefixes;
    }

    for (const auto digit : hex_digits) {
        auto first = HashPrefix::parse(string(1, digit));

        if (first.matching_bits(prefix) >= least) {
            prefixes.push_back(std::move(first));
        }
    }

    return prefixes;
}

// What the node holds, as held lists it, that shares least leading bits
// with prefix.
vector<Identifier> held_matching(const SearchAnswers::Held& held,
                                 const HashPrefix& prefix,
                                 size_t least) {
    vector<Identifier> found;

    for (const auto& scanned_prefix : scanned(prefix, least)) {
        auto listed = held(scanned_prefix);

        found.insert(found.end(), make_move_iterator(listed.begin()),
                     make_move_iterator(listed.end()));
    }

    return matching(prefix, std::move(found), least);
}

} // namespace

namespace storage {

SearchAnswers::SearchAnswers(Held held, size_t min_bits, Milliseconds ttl, ClockFunction clock)
    : m_held(std::move(held))
    , m_min_bits(min_bits)
    , m_ttl(ttl)
    , m_clock(std::move(clock)) {
    if (!m_held || !m_clock) {
        throw invalid_argument("Searches need what the node holds, and the time");
    }

    if (m_ttl <= Milliseconds::zero()) {
        throw invalid_argument("An answer must be kept for more than no time");
    }
}

vector<Identifier> SearchAnswers::answer(const HashPrefix& prefix) {
    auto kept = _kept(prefix);

    if (kept) {
        return std::move(*kept);
    }

    return _merge(prefix, held_matching(m_held, prefix, least_bits(prefix, m_min_bits)));
}

void SearchAnswers::add(const HashPrefix& prefix, const vector<Identifier>& named) {
    // Made first, when none is kept.
    (void)answer(prefix);
    (void)_merge(prefix, matching(prefix, named, least_bits(prefix, m_min_bits)));
}

// The answer kept for prefix, if it is still fresh.
optional<vector<Identifier>> SearchAnswers::_kept(const HashPrefix& prefix) {
    const auto now = m_clock();
    const scoped_lock lock(m_mutex);
    const auto kept = m_answers.find(prefix.to_string());

    if (kept == m_answers.end() || !is_fresh(kept->second.changed, now, m_ttl)) {
        return nullopt;
    }

    return kept->second.identifiers;
}

// Adds found, which all match prefix, to the answer kept for prefix, or
// keeps them as its answer when none is fresh, and gives the answer. It
// changes, and is kept from now, only when it names more or better.
vector<Identifier> SearchAnswers::_merge(const HashPrefix& prefix,
                                         vector<Identifier> found) {
    const auto now = m_clock();
    const scoped_lock lock(m_mutex);

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
        || !equal(merged.begin(), merged.end(), kept->second.identifiers.begin(),
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
