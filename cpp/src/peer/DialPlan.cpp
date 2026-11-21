#include "DialPlan.hpp"

#include <algorithm>
#include <cstddef>
#include <functional>
#include <iterator>
#include <map>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "Buckets.hpp"
#include "storage/Identifier.hpp"

using peer::Buckets;
using peer::DialPlan;
using std::function;
using std::inserter;
using std::invalid_argument;
using std::map;
using std::max;
using std::optional;
using std::set;
using std::size_t;
using std::string;
using std::transform;
using std::vector;
using storage::Identifier;

namespace {

using Candidate = DialPlan::Candidate;
using Clock = DialPlan::Clock;
using Milliseconds = DialPlan::Milliseconds;
// Until when each node rests, by node id.
using Resting = map<string, Clock::time_point>;
// The bucket of one set a node falls in, or none for a node with no place in
// it.
using Place = function<optional<size_t>(const Identifier&)>;

// Throws invalid_argument, naming setting, for a rest that is not more than
// zero, as the Python node refuses one.
void check_rest(Milliseconds rest, const string& setting) {
    if (rest <= Milliseconds::zero()) {
        throw invalid_argument(setting + " is not more than zero");
    }
}

// rests, once checked.
DialPlan::Rests checked(const DialPlan::Rests& rests) {
    check_rest(rests.retry_delay, "peers.retry_delay_seconds");
    check_rest(rests.turned_away_rest, "peers.turned_away_rest_seconds");
    return rests;
}

// The ids, as text, of the nodes not to dial at now: this node, own_id; the
// server nodes, server_ids; and those resting.
set<string> passed_over(const string& own_id, const vector<Identifier>& server_ids,
                        const Resting& resting, Clock::time_point now) {
    set<string> node_ids{own_id};

    transform(server_ids.begin(), server_ids.end(), inserter(node_ids, node_ids.end()),
              [](const Identifier& server_id) { return server_id.to_string(); });

    for (const auto& [node_id, until] : resting) {
        if (now < until) {
            node_ids.insert(node_id);
        }
    }

    return node_ids;
}

// The first of candidates in each of missing's buckets, by place, until
// missing wants no more, passing over those in taken. Those chosen are added
// to taken, so that a later set passes over them too.
vector<Candidate> spread(const vector<Candidate>& candidates, const Buckets::Missing& missing,
                         const Place& place, set<string>& taken) {
    set<size_t> empty(missing.buckets.begin(), missing.buckets.end());
    vector<Candidate> chosen;

    for (const auto& candidate : candidates) {
        if (chosen.size() >= missing.wanted) {
            break;
        }

        auto node_id = candidate.node_id.to_string();

        if (0 != taken.count(node_id)) {
            continue;
        }

        const auto bucket = place(candidate.node_id);

        if (!bucket || 0 == empty.count(*bucket)) {
            continue;
        }

        empty.erase(*bucket);
        taken.insert(std::move(node_id));
        chosen.push_back(candidate);
    }

    return chosen;
}

// Lets go of the nodes whose rest is over by now, so that resting holds only
// those rested lately.
void let_go(Resting& resting, Clock::time_point now) {
    for (auto entry = resting.begin(); entry != resting.end();) {
        if (entry->second <= now) {
            entry = resting.erase(entry);
        } else {
            ++entry;
        }
    }
}

// Rests node_id until rest after now, unless it rests longer already.
void rest_node(Resting& resting, const Identifier& node_id, Clock::time_point now,
               Milliseconds rest) {
    const auto until = now + rest;

    let_go(resting, now);

    const auto [entry, added] = resting.emplace(node_id.to_string(), until);

    if (!added) {
        entry->second = max(entry->second, until);
    }
}

} // namespace

namespace peer {

DialPlan::DialPlan(const Identifier& own_id, const Buckets::ExpectedSizes& sizes,
                   const Rests& rests, Now now)
    : m_buckets(own_id, sizes)
    , m_own_id(own_id.to_string())
    , m_rests(checked(rests))
    , m_now(std::move(now)) {
    if (!m_now) {
        throw invalid_argument("A dial plan needs the time");
    }
}

vector<Candidate> DialPlan::to_dial(const vector<Candidate>& candidates,
                                    const vector<Identifier>& server_ids) const {
    const auto gaps = m_buckets.gaps(server_ids);
    const Place primary = [this](const Identifier& node_id) -> optional<size_t> {
        return m_buckets.primary_bucket(node_id);
    };
    const Place secondary = [this](const Identifier& node_id) {
        return m_buckets.secondary_bucket(node_id);
    };
    auto taken = passed_over(m_own_id, server_ids, m_resting, m_now());
    auto chosen = spread(candidates, gaps.primary, primary, taken);
    const auto neighbors = spread(candidates, gaps.secondary, secondary, taken);

    chosen.insert(chosen.end(), neighbors.begin(), neighbors.end());
    return chosen;
}

void DialPlan::rest_failed(const Identifier& node_id) {
    rest_node(m_resting, node_id, m_now(), m_rests.retry_delay);
}

void DialPlan::rest_turned_away(const Identifier& node_id) {
    rest_node(m_resting, node_id, m_now(), m_rests.turned_away_rest);
}

} // namespace peer
