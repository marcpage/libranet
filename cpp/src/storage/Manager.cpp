#include "Manager.hpp"

#include <algorithm>
#include <filesystem>
#include <memory>
#include <optional>
#include <stdexcept>
#include <utility>

#include "DataDir.hpp"
#include "IDataSource.hpp"
#include "Identifier.hpp"

using std::invalid_argument;
using std::nullopt;
using std::optional;
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

    for (auto const& dir_entry : directory_iterator(directory)) {
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

Identifier Manager::put(const Data& data) const {
    if (m_sources.empty()) {
        throw invalid_argument("Trying to add data before first store added");
    }

    return m_sources.front()->put(data);
}

} // namespace storage
