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

    IDataSource() = default;
    virtual ~IDataSource() = default;

    [[nodiscard]] virtual bool has(const Identifier& identifier) const = 0;
    // The body stored under identifier, as it was put.
    [[nodiscard]] virtual std::optional<Data> get(const Identifier& identifier) const = 0;
    // Stores body under identifier, as it is, in place of any body stored
    // under it before: the content identifier names, or that content
    // compressed, so that content over 1 MiB is kept compressed
    // (HighLevelDesign §4.3). The body is not checked against identifier;
    // Identifier::content_of() checks it.
    virtual void put(const Identifier& identifier, const Data& body) const = 0;
    // Whether there was a body stored under identifier to remove.
    [[nodiscard]] virtual bool remove(const Identifier& identifier) const = 0;
    // How many bytes the bodies held take, as stored.
    [[nodiscard]] virtual std::uintmax_t size() const = 0;
    // The identifiers of what is held whose hash begins with prefix, in no
    // particular order.
    [[nodiscard]] virtual Identifiers starting_with(const HashPrefix& prefix) const = 0;

    IDataSource(const IDataSource&) = delete;
    IDataSource& operator=(const IDataSource&) = delete;
    IDataSource(const IDataSource&&) = delete;
    IDataSource& operator=(IDataSource&&) = delete;
};

} // namespace storage
