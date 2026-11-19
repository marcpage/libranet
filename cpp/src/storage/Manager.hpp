#pragma once

#include <filesystem>
#include <memory>
#include <vector>

#include "storage/IDataSource.hpp"

namespace storage {

// aggregates sources, reads from all, writes to first added
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
    [[nodiscard]] Identifier put(const Data& data) const override;

    Manager() = default;
    Manager(const Manager&) = delete;
    Manager& operator=(const Manager&) = delete;
    Manager(const Manager&&) = delete;
    Manager& operator=(Manager&&) = delete;

private:
    std::vector<IDataSource::Ptr> m_sources;
};

} // namespace storage
