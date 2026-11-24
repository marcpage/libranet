#include "Seeker.hpp"

#include <algorithm>
#include <cstddef>
#include <exception>
#include <functional>
#include <future>
#include <list>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "Fetch.hpp"
#include "PeerSearch.hpp"
#include "logger/Logger.hpp"
#include "msg/IMessage.hpp"
#include "msg/Message.hpp"
#include "msg/Timer.hpp"
#include "stats/Recorder.hpp"
#include "storage/DataNotFound.hpp"
#include "storage/Identifier.hpp"

using msg::IMessage;
using msg::Timer;
using msg::value_of;
using peer::Fetch;
using peer::PeerSearch;
using peer::Seeker;
using std::any_of;
using std::exception;
using std::function;
using std::future;
using std::future_status;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::scoped_lock;
using std::size_t;
using std::string;
using std::to_string;
using storage::DataNotFound;
using storage::Identifier;
using SearchRequested = stats::Recorder::SearchRequested;

// The thread takes these events, which api::DataHandler and
// api::SearchHandler post from other files, so nothing here would make
// their messages' virtual to_string(), which value_of() casts to.
template class msg::Message<DataNotFound>;
template class msg::Message<SearchRequested>;

namespace {

using Milliseconds = Seeker::Milliseconds;

size_t checked_workers(size_t workers) {
    if (0 == workers) {
        throw invalid_argument("peers.search_workers must be at least 1");
    }

    return workers;
}

Milliseconds checked_interval(Milliseconds interval) {
    if (interval <= Milliseconds::zero()) {
        throw invalid_argument("The interval passes are looked for at is not more than zero: "
                               + to_string(interval.count()) + " ms");
    }

    return interval;
}

Seeker::Peers checked_peers(Seeker::Peers peers) {
    if (!peers) {
        throw invalid_argument("A seeker needs the peers to search");
    }

    return peers;
}

// Reports what the thread could not handle.
void log_error(const string& text) {
    lErr << text;
}

} // namespace

namespace peer {

Seeker::Seeker(Fetch& fetch, Peers peers, const Settings& settings, const Now& now)
    : m_fetch(&fetch)
    , m_peers(checked_peers(std::move(peers)))
    , m_workers(checked_workers(settings.workers))
    , m_now(now)
    , m_search(settings.search, now)
    , m_channel({DataNotFound::name, SearchRequested::name, pass_due},
                [this](const IMessage::Ptr& message) { _handle(message); }, log_error)
    , m_timer(pass_due, checked_interval(settings.check_interval)) {}

Seeker::~Seeker() {
    {
        const scoped_lock lock(m_mutex);

        m_stopping = true;
        m_waiting.clear();
    }

    m_timer.stop();
    m_channel.stop();
    m_fetch->stop();

    // No job starts once it is stopping, so those taken are all there are.
    std::list<future<void>> jobs;

    {
        const scoped_lock lock(m_mutex);
        jobs.swap(m_jobs);
    }

    for (const auto& job : jobs) {
        job.wait();
    }
}

void Seeker::_handle(const IMessage::Ptr& message) {
    if (const auto* const lacked = value_of<DataNotFound>(message)) {
        _lacked(lacked->identifier);
    } else if (const auto* const requested = value_of<SearchRequested>(message)) {
        _search(*requested);
    } else if (nullptr != value_of<Timer::Tick>(message)) {
        _pass_if_due();
    } else {
        lWarn << "Not an event the seeker takes:" << message->to_string();
    }
}

// Fetches identifier's content, on a thread of its own, unless as many
// fetches are under way as may be, when it waits its turn, unless it waits
// already.
void Seeker::_lacked(const Identifier& identifier) {
    const scoped_lock lock(m_mutex);

    if (m_stopping) {
        return;
    }

    if (m_fetching >= m_workers) {
        const auto key = identifier.to_string();
        const auto waiting = any_of(m_waiting.begin(), m_waiting.end(),
                                    [&key](const Identifier& waits) {
            return waits.to_string() == key;
        });

        if (!waiting) {
            m_waiting.push_back(identifier);
        }

        return;
    }

    ++m_fetching;
    _start([this, to_fetch = identifier] { _fetch_from(to_fetch); });
}

// Starts a search of the peers for what requested names, and makes its first
// pass at once, unless passes are being made, when it is made once they are.
void Seeker::_search(const SearchRequested& requested) {
    optional<PeerSearch::Asker> asker;

    if (requested.node) {
        asker = PeerSearch::Asker{*requested.node, requested.relays};
    }

    if (!m_search.start(requested.prefix, asker)) {
        return;
    }

    const scoped_lock lock(m_mutex);

    if (m_passing) {
        m_pass_again = true;
        return;
    }

    _pass();
}

// Makes the passes due now, unless they are being made, or none is due.
void Seeker::_pass_if_due() {
    const scoped_lock lock(m_mutex);

    if (m_passing || !m_due || m_now() < *m_due) {
        return;
    }

    _pass();
}

// Makes the passes due now, on a thread of its own. Called holding m_mutex.
void Seeker::_pass() {
    if (m_stopping) {
        return;
    }

    m_passing = true;
    m_due.reset();
    _start([this] { _make_passes(); });
}

// Fetches identifier's content, then each that waits its turn, until none
// waits or it is stopping.
void Seeker::_fetch_from(Identifier identifier) {
    optional<Identifier> next = std::move(identifier);

    while (next) {
        try {
            (void)m_fetch->fetch(*next);
        } catch (const exception& error) {
            lErr << "Could not fetch" << next->to_string() + ":" << error.what();
        }

        const scoped_lock lock(m_mutex);

        if (m_stopping || m_waiting.empty()) {
            --m_fetching;
            next = nullopt;
        } else {
            next = m_waiting.front();
            m_waiting.pop_front();
        }
    }
}

// Makes each pass due now, and notes when the next is due: at once, for a
// search that began while they were made, as they may have passed it over.
void Seeker::_make_passes() {
    optional<Clock::time_point> next;

    try {
        next = m_search.make_passes(m_peers());
    } catch (const exception& error) {
        lErr << "Could not search the peers:" << error.what();
    }

    const scoped_lock lock(m_mutex);

    m_passing = false;
    m_due = m_pass_again ? m_now() : next;
    m_pass_again = false;
}

// Runs job on a thread of its own, unless it is stopping, and lets go of
// the jobs that have ended. Called holding m_mutex.
void Seeker::_start(function<void()> job) {
    if (m_stopping) {
        return;
    }

    m_jobs.remove_if([](const future<void>& started) {
        return future_status::ready == started.wait_for(Milliseconds::zero());
    });
    m_jobs.push_back(std::async(std::launch::async, [job = std::move(job)] {
        try {
            job();
        } catch (const exception& error) {
            lErr << "The seeker could not finish:" << error.what();
        }
    }));
}

} // namespace peer
