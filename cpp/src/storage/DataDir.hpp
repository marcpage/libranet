#pragma once

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <ios>
#include <memory>
#include <numeric>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#include "HashPrefix.hpp"
#include "IDataSource.hpp"
#include "Identifier.hpp"
#include "io/SafeWriteFile.hpp"
#include "logger/Logger.hpp"

namespace storage {

// Each body is kept as it was put, in a file named by its identifier's hash,
// in a directory named by the hash's first three digits, in a directory
// named by the algorithm. A body is written to a file of its own beside
// where it goes, then renamed into place, so that no reader sees one half
// written.
class DataDir : public IDataSource {
public:
    using Data = std::vector<std::byte>;

    static IDataSource::Ptr create(std::filesystem::path path);
    explicit DataDir(std::filesystem::path path);
    ~DataDir() override = default;

    [[nodiscard]] bool has(const Identifier& identifier) const override;
    [[nodiscard]] std::optional<Data> get(const Identifier& identifier) const override;
    // Throws std::runtime_error when the body cannot be written into place.
    void put(const Identifier& identifier, const Data& body) const override;
    // Throws std::runtime_error when the body is there but cannot be removed.
    [[nodiscard]] bool remove(const Identifier& identifier) const override;
    [[nodiscard]] std::uintmax_t size() const override;
    [[nodiscard]] Identifiers starting_with(const HashPrefix& prefix) const override;

    DataDir() = delete;
    DataDir(const DataDir&) = delete;
    DataDir& operator=(const DataDir&) = delete;
    DataDir(const DataDir&&) = delete;
    DataDir& operator=(DataDir&&) = delete;

private:
    // A body held, and how many bytes it takes.
    struct Held {
        Identifier identifier;
        std::uintmax_t size = 0;
    };

    // How many of a hash's digits name the directory it is kept in.
    static constexpr std::size_t directory_digits = 3;

    std::filesystem::path m_directory;

    [[nodiscard]] std::filesystem::path _path(const Identifier& identifier) const;
    [[nodiscard]] std::vector<Held> _held(std::string_view digits) const;
    void _add_held(const std::filesystem::path& directory, const std::string& algorithm,
                   std::string_view digits, std::vector<Held>& held) const;
    [[nodiscard]] std::optional<Held> _held_at(const std::string& algorithm,
                                               const std::filesystem::directory_entry& entry) const;
    [[nodiscard]] static std::vector<std::filesystem::path>
    _subdirectories(const std::filesystem::path& directory);
    [[nodiscard]] static bool _write(const std::filesystem::path& partial, const Data& body);
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
    const auto length = std::filesystem::file_size(path, error);

    if (error) {
        return std::nullopt;
    }

    std::ifstream file(path, std::ios::binary);
    Data buffer(length);

    if (!file) {
        return std::nullopt;
    }

    if (length > 0
        // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
        && !file.read(reinterpret_cast<char*>(buffer.data()),
                      static_cast<std::streamsize>(length))) {
        return std::nullopt;
    }

    return buffer;
}

inline void DataDir::put(const Identifier& identifier, const Data& body) const {
    io::SafeWriteFile file(_path(identifier));

    if (!_write(file.writable_path(), body)) {
        throw std::runtime_error("Failed to write file: " + file.writable_path().string());
    }

    file.commit();
}

// Only a file is a body, so a directory in its place is left.
inline bool DataDir::remove(const Identifier& identifier) const {
    const auto path = _path(identifier);
    std::error_code error;

    if (!has(identifier)) {
        return false;
    }

    const auto removed = std::filesystem::remove(path, error);

    if (error) {
        throw std::runtime_error("Failed to remove file: " + path.string() + ": "
                                 + error.message());
    }

    return removed;
}

inline std::uintmax_t DataDir::size() const {
    const auto held = _held("");

    return std::accumulate(held.begin(), held.end(), std::uintmax_t{0},
                           [](std::uintmax_t total, const Held& body) {
        return total + body.size;
    });
}

inline DataDir::Identifiers DataDir::starting_with(const HashPrefix& prefix) const {
    Identifiers identifiers;

    for (auto& held : _held(prefix.to_string())) {
        identifiers.push_back(std::move(held.identifier));
    }

    return identifiers;
}

inline std::filesystem::path DataDir::_path(const Identifier& identifier) const {
    return m_directory / identifier.algorithm()
           / identifier.hash().substr(0, directory_digits) / identifier.hash();
}

// What is held whose hash starts with digits, in lower-case hex, or all that
// is held for none. Only the directories of supported algorithms, and of
// them only those that can hold such a hash, are read.
inline std::vector<DataDir::Held> DataDir::_held(std::string_view digits) const {
    const auto directory_start = digits.substr(0, directory_digits);
    std::vector<Held> held;

    for (const auto& algorithm : _subdirectories(m_directory)) {
        if (!Identifier::is_supported(algorithm.filename().string())) {
            continue;
        }

        for (const auto& directory : _subdirectories(algorithm)) {
            const auto name = directory.filename().string();

            if (0 == name.compare(0, directory_start.size(), directory_start)) {
                _add_held(directory, algorithm.filename().string(), digits, held);
            }
        }
    }

    return held;
}

// Adds what directory, of algorithm's, holds whose hash starts with digits.
// Anything else there whose name starts with them is warned of and skipped,
// but for a body being written, and for what is removed while it is read.
inline void DataDir::_add_held(const std::filesystem::path& directory,
                               const std::string& algorithm, std::string_view digits,
                               std::vector<Held>& held) const {
    std::error_code error;

    for (const auto& entry : std::filesystem::directory_iterator(directory, error)) {
        const auto name = entry.path().filename().string();

        if (0 != name.compare(0, digits.size(), digits)
            || io::SafeWriteFile::is_partial(entry.path())) {
            continue;
        }

        auto found = _held_at(algorithm, entry);

        if (found) {
            held.push_back(std::move(*found));
        } else if (entry.exists(error)) {
            lWarn << "Skipping what the store does not keep as a body:" << entry.path().string();
        }
    }
}

// The body held at entry, in a directory of algorithm's: nothing when entry
// is not a file, is gone, or is not where the store keeps the body of the
// identifier its name gives, as a hash in upper case, or in the wrong
// directory, is not.
inline std::optional<DataDir::Held>
DataDir::_held_at(const std::string& algorithm,
                  const std::filesystem::directory_entry& entry) const {
    std::error_code error;

    if (!entry.is_regular_file(error)) {
        return std::nullopt;
    }

    const auto length = entry.file_size(error);

    if (error) {
        return std::nullopt;
    }

    try {
        Identifier identifier(algorithm, entry.path().filename().string());

        if (_path(identifier) == entry.path()) {
            return Held{std::move(identifier), length};
        }
    } catch (const std::invalid_argument&) {
        // Not named by a hash of the algorithm: no body held.
        return std::nullopt;
    }

    return std::nullopt;
}

// The directories in directory, or none when it cannot be read, as when
// nothing has been put there yet.
inline std::vector<std::filesystem::path>
DataDir::_subdirectories(const std::filesystem::path& directory) {
    std::vector<std::filesystem::path> found;
    std::error_code error;

    for (const auto& entry : std::filesystem::directory_iterator(directory, error)) {
        if (entry.is_directory(error)) {
            found.push_back(entry.path());
        }
    }

    return found;
}

// Writes body to a new file at partial, and closes it. Whether all of it was
// written.
inline bool DataDir::_write(const std::filesystem::path& partial, const Data& body) {
    std::ofstream file(partial, std::ios::binary | std::ios::trunc);

    if (!body.empty()) {
        // NOLINTNEXTLINE(cppcoreguidelines-pro-type-reinterpret-cast)
        file.write(reinterpret_cast<const char*>(body.data()),
                   static_cast<std::streamsize>(body.size()));
    }

    file.close();
    return !file.fail();
}

} // namespace storage
