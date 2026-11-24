#pragma once

#include <gtest/gtest.h>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <map>
#include <memory>
#include <optional>
#include <random>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#include "logger/ILogOutput.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"

// What more than one of the storage tests uses: some data and its hash, bytes
// written as text, a directory of a test's own to store them in, a source of
// data that keeps it in memory, and a log output that keeps what is logged.
namespace fixtures {

// "hello", and its SHA-256 digest, as `shasum -a 256` gives it.
inline constexpr auto hello = "hello";
inline constexpr auto hello_hash =
    "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";

// The bytes of text, a byte for each char.
inline std::vector<std::byte> bytes_of(std::string_view text) {
    std::vector<std::byte> bytes;

    bytes.reserve(text.size());
    std::transform(text.begin(), text.end(), std::back_inserter(bytes), [](char character) {
        return static_cast<std::byte>(character);
    });
    return bytes;
}

// The file at path, with text in it, and the directories it is in.
inline void write_file(const std::filesystem::path& path, std::string_view text) {
    std::filesystem::create_directories(path.parent_path());

    std::ofstream file(path, std::ios::binary | std::ios::trunc);

    file << text;
}

// A directory of the running test's own, made under the system's directory
// for temporary files, and removed with all it holds when this is destroyed.
// Its name holds the test's and a random number, so that two runs of the
// tests at once do not share one.
class ScratchDirectory {
public:
    ScratchDirectory();
    ~ScratchDirectory();

    [[nodiscard]] const std::filesystem::path& path() const noexcept;

    ScratchDirectory(const ScratchDirectory&) = delete;
    ScratchDirectory& operator=(const ScratchDirectory&) = delete;
    ScratchDirectory(ScratchDirectory&&) = delete;
    ScratchDirectory& operator=(ScratchDirectory&&) = delete;

private:
    std::filesystem::path m_path;
};

inline ScratchDirectory::ScratchDirectory() {
    const auto* const test = testing::UnitTest::GetInstance()->current_test_info();
    std::random_device random;

    m_path = std::filesystem::temp_directory_path()
             / (std::string("libranet-") + test->test_suite_name() + "-" + test->name() + "-"
                + std::to_string(random()));
    std::filesystem::create_directories(m_path);
}

inline ScratchDirectory::~ScratchDirectory() {
    std::error_code error;

    std::filesystem::remove_all(m_path, error);
}

inline const std::filesystem::path& ScratchDirectory::path() const noexcept {
    return m_path;
}

// A source that keeps what it is given in memory, by identifier. put() is
// const in IDataSource, so what it keeps is mutable.
class MemorySource : public storage::IDataSource {
public:
    [[nodiscard]] bool has(const storage::Identifier& identifier) const override;
    [[nodiscard]] std::optional<Data> get(const storage::Identifier& identifier) const override;
    void put(const storage::Identifier& identifier, const Data& body) const override;
    [[nodiscard]] bool remove(const storage::Identifier& identifier) const override;
    [[nodiscard]] std::uintmax_t size() const override;
    [[nodiscard]] Identifiers starting_with(const storage::HashPrefix& prefix) const override;

private:
    mutable std::map<std::string, Data> m_data;
};

inline bool MemorySource::has(const storage::Identifier& identifier) const {
    return m_data.find(identifier.to_string()) != m_data.end();
}

inline std::optional<storage::IDataSource::Data> MemorySource::get(const storage::Identifier& identifier) const {
    const auto found = m_data.find(identifier.to_string());

    if (found == m_data.end()) {
        return std::nullopt;
    }

    return found->second;
}

inline void MemorySource::put(const storage::Identifier& identifier, const Data& body) const {
    m_data[identifier.to_string()] = body;
}

inline bool MemorySource::remove(const storage::Identifier& identifier) const {
    return m_data.erase(identifier.to_string()) > 0;
}

inline std::uintmax_t MemorySource::size() const {
    std::uintmax_t total = 0;

    for (const auto& [name, body] : m_data) {
        total += body.size();
    }

    return total;
}

inline storage::IDataSource::Identifiers
MemorySource::starting_with(const storage::HashPrefix& prefix) const {
    Identifiers found;

    for (const auto& [name, body] : m_data) {
        auto identifier = storage::Identifier::parse(name);

        if (prefix.matching_bits(identifier) >= prefix.bits()) {
            found.push_back(std::move(identifier));
        }
    }

    return found;
}

// Keeps each line it is given, where a test can read them while the Logger
// owns it.
class KeepingOutput : public logger::ILogOutput {
public:
    explicit KeepingOutput(std::shared_ptr<std::vector<std::string>> lines)
        : m_lines(std::move(lines)) {
    }

    void log(std::string_view line) override {
        m_lines->emplace_back(line);
    }

private:
    std::shared_ptr<std::vector<std::string>> m_lines;
};

} // namespace fixtures
