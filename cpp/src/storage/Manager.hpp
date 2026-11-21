#pragma once

#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <vector>

#include "HashPrefix.hpp"
#include "IDataSource.hpp"
#include "Identifier.hpp"

namespace storage {

// aggregates sources, reads from all, writes to first added, and removes
// from all, so that what is removed is not still found in another
class Manager : public IDataSource {
public:
    using Ptr = std::shared_ptr<Manager>;
    static Ptr create();
    ~Manager() override = default;

    void add(std::filesystem::path directory);
    void add(IDataSource::Ptr source);
    void scan(const std::filesystem::path& directory);

    [[nodiscard]] bool has(const Identifier& identifier) const override;
    [[nodiscard]] std::optional<Data> get(const Identifier& identifier) const override;
    void put(const Identifier& identifier, const Data& body) const override;
    [[nodiscard]] bool remove(const Identifier& identifier) const override;
    [[nodiscard]] std::uintmax_t size() const override;
    // Each identifier once, though more than one source holds it.
    [[nodiscard]] Identifiers starting_with(const HashPrefix& prefix) const override;

    Manager() = default;
    Manager(const Manager&) = delete;
    Manager& operator=(const Manager&) = delete;
    Manager(const Manager&&) = delete;
    Manager& operator=(Manager&&) = delete;

private:
    std::vector<IDataSource::Ptr> m_sources;
};

} // namespace storage
