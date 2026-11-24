#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <vector>

#include "HashPrefix.hpp"
#include "Identifier.hpp"

namespace storage {

class IDataSource {
public:
    using Ptr = std::shared_ptr<IDataSource>;
    using Data = std::vector<std::byte>;
    using Identifiers = std::vector<Identifier>;

    [[nodiscard]] virtual bool has(const Identifier& identifier) const = 0;
    // As it was put.
    [[nodiscard]] virtual std::optional<Data> get(const Identifier& identifier) const = 0;
    // body is the content identifier names, or that content compressed, as
    // content over 1 MiB is (HighLevelDesign §4.3). It is not checked against
    // identifier; Identifier::content_of() checks it.
    virtual void put(const Identifier& identifier, const Data& body) const = 0;
    // Whether there was a body to remove.
    [[nodiscard]] virtual bool remove(const Identifier& identifier) const = 0;
    // As stored.
    [[nodiscard]] virtual std::uintmax_t size() const = 0;
    // In no particular order.
    [[nodiscard]] virtual Identifiers starting_with(const HashPrefix& prefix) const = 0;

    IDataSource() = default;
    virtual ~IDataSource() = default;

    IDataSource(const IDataSource&) = delete;
    IDataSource& operator=(const IDataSource&) = delete;
    IDataSource(IDataSource&&) = delete;
    IDataSource& operator=(IDataSource&&) = delete;
};

} // namespace storage
