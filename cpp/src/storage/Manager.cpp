#include "Manager.hpp"

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <numeric>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>

#include "DataDir.hpp"
#include "HashPrefix.hpp"
#include "IDataSource.hpp"
#include "Identifier.hpp"

using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::set;
using std::string;
using std::uintmax_t;
using std::filesystem::directory_iterator;
using std::filesystem::path;

namespace storage {

Manager::Ptr Manager::create() {
    return std::make_shared<Manager>();
}

void Manager::add(path directory) {
    add(DataDir::create(std::move(directory)));
}

void Manager::add(IDataSource::Ptr source) {
    m_sources.push_back(std::move(source));
}

void Manager::scan(const path& directory) {
    if (!std::filesystem::exists(directory)) {
        return;
    }

    for (const auto& dir_entry : directory_iterator(directory)) {
        if (dir_entry.is_directory()) {
            add(dir_entry.path());
        }
    }
}

bool Manager::has(const Identifier& identifier) const {
    return std::any_of(m_sources.begin(), m_sources.end(),
                       [&identifier](const auto& store) {
        return store->has(identifier);
    });
}

optional<IDataSource::Data> Manager::get(const Identifier& identifier) const {
    for (const auto& store : m_sources) {
        auto value = store->get(identifier);

        if (value.has_value()) {
            return value;
        }
    }

    return nullopt;
}

void Manager::put(const Identifier& identifier, const Data& body) const {
    if (m_sources.empty()) {
        throw invalid_argument("Trying to add data before first store added");
    }

    m_sources.front()->put(identifier, body);
}

bool Manager::remove(const Identifier& identifier) const {
    auto removed = false;

    for (const auto& store : m_sources) {
        removed = store->remove(identifier) || removed;
    }

    return removed;
}

uintmax_t Manager::size() const {
    return std::accumulate(m_sources.begin(), m_sources.end(), uintmax_t{0},
                           [](uintmax_t total, const auto& store) {
        return total + store->size();
    });
}

IDataSource::Identifiers Manager::starting_with(const HashPrefix& prefix) const {
    Identifiers found;
    set<string> named;

    for (const auto& store : m_sources) {
        for (auto& identifier : store->starting_with(prefix)) {
            if (named.insert(identifier.to_string()).second) {
                found.push_back(std::move(identifier));
            }
        }
    }

    return found;
}

} // namespace storage
