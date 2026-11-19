#pragma once

#include <filesystem>
#include <fstream>
#include <memory>
#include <optional>
#include <stdexcept>
#include <system_error>

#include "IDataSource.hpp"
#include "Identifier.hpp"

namespace storage {

class DataDir : public IDataSource {
public:
    using Data = std::vector<std::byte>;

    static IDataSource::Ptr create(std::filesystem::path path);
    explicit DataDir(std::filesystem::path path);
    ~DataDir() override = default;

    [[nodiscard]] bool has(const Identifier& identifier) const override;
    [[nodiscard]] std::optional<Data> get(const Identifier& identifier) const override;
    [[nodiscard]] Identifier put(const Data& data) const override;

    DataDir() = delete;
    DataDir(const DataDir&) = delete;
    DataDir& operator=(const DataDir&) = delete;
    DataDir(const DataDir&&) = delete;
    DataDir& operator=(DataDir&&) = delete;

private:
    std::filesystem::path m_directory;

    [[nodiscard]] std::filesystem::path _path(const Identifier& identifier) const;
};

inline DataDir::Ptr DataDir::create(std::filesystem::path path) {
    return std::make_shared<DataDir>(std::move(path));
}

inline DataDir::DataDir(std::filesystem::path path)
    : m_directory(std::move(path)) {}

inline bool DataDir::has(const Identifier& identifier) const {
    return std::filesystem::is_regular_file(_path(identifier));
}

inline std::optional<DataDir::Data> DataDir::get(const Identifier& identifier) const {
    const auto path = _path(identifier);
    std::error_code error;
    const auto size = std::filesystem::file_size(path, error);

    if (error) {
        return std::nullopt;
    }

    std::ifstream file(path, std::ios::binary);
    Data buffer(size);

    if (!file) {
        return std::nullopt;
    }

    // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
    if (size > 0 && !file.read(reinterpret_cast<char*>(buffer.data()), static_cast<std::streamsize>(size))) {
        return std::nullopt;
    }

    return buffer;
}

inline Identifier DataDir::put(const Data& data) const {
    auto identifier = Identifier::from(data);
    const auto path = _path(identifier);
    std::error_code error;

    std::filesystem::create_directories(path.parent_path(), error);

    if (error) {
        throw std::runtime_error("Failed to create directory: " + path.parent_path().string() + ": "
                                 + error.message());
    }

    std::ofstream file(path, std::ios::binary | std::ios::trunc);

    if (!file) {
        throw std::runtime_error("Failed to open file for writing: " + path.string());
    }

    if (!data.empty()
        // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
        && !file.write(reinterpret_cast<const char*>(data.data()),
                       static_cast<std::streamsize>(data.size()))) {
        throw std::runtime_error("Failed to write file contents: " + path.string());
    }

    file.flush();

    if (!file) {
        throw std::runtime_error("Failed to flush file contents: " + path.string());
    }

    return identifier;
}

inline std::filesystem::path DataDir::_path(const Identifier& identifier) const {
    return m_directory / identifier.algorithm() / identifier.hash().substr(0, 3) / identifier.hash();
}

} // namespace storage
