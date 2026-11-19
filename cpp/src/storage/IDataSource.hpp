#pragma once

#include <memory>
#include <optional>

#include "Identifier.hpp"

namespace storage {

class IDataSource {
public:
    using Ptr = std::shared_ptr<IDataSource>;
    using Data = std::vector<std::byte>;

    IDataSource() = default;
    virtual ~IDataSource() = default;

    [[nodiscard]] virtual bool has(const Identifier& identifier) const = 0;
    [[nodiscard]] virtual std::optional<Data> get(const Identifier& identifier) const = 0;
    [[nodiscard]] virtual Identifier put(const Data& data) const = 0;

    IDataSource(const IDataSource&) = delete;
    IDataSource& operator=(const IDataSource&) = delete;
    IDataSource(const IDataSource&&) = delete;
    IDataSource& operator=(IDataSource&&) = delete;
};

} // namespace storage
