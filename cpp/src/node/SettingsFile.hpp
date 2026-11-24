#pragma once

#include <cstddef>
#include <filesystem>
#include <map>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

namespace node {

// The settings a YAML file gives, laid out as the Python node's libranet.yaml
// is. A setting is named by its section and name, as
// "storage.max_storage_bytes", and kept as the text the file gives.
class SettingsFile {
public:
    using List = std::vector<std::string>;
    using Value = std::variant<std::string, List>;

    // Far more than the Python node's few hundred settings, and a bound on
    // what an alias that repeats a list can make it hold.
    static constexpr std::size_t max_values = 10'000;

    // A document with nothing in it has no settings. Throws
    // std::invalid_argument for text that is not one YAML document of sections
    // of settings, holds what is not UTF-8, or gives more than max_values
    // values.
    [[nodiscard]] static SettingsFile parse(std::string_view text);

    // Throws std::runtime_error if it cannot be read, and
    // std::invalid_argument, naming the file, for what parse() refuses.
    [[nodiscard]] static SettingsFile load(const std::filesystem::path& path);

    SettingsFile() = default;

    // In order.
    [[nodiscard]] std::vector<std::string> names() const;
    [[nodiscard]] bool has(const std::string& name) const;

    // Throws std::out_of_range if there is no such setting, and
    // std::domain_error if it is a list.
    [[nodiscard]] const std::string& value(const std::string& name) const;

    // Throws std::out_of_range if there is no such setting, and
    // std::domain_error if it is a single value.
    [[nodiscard]] const List& list(const std::string& name) const;

    // As YAML, which parse() reads back as the same settings.
    [[nodiscard]] std::string to_string() const;

    friend bool operator==(const SettingsFile& left, const SettingsFile& right);
    friend bool operator!=(const SettingsFile& left, const SettingsFile& right);

private:
    std::map<std::string, Value> m_settings;

    explicit SettingsFile(std::map<std::string, Value> settings);

    [[nodiscard]] const Value& _at(const std::string& name) const;
};

} // namespace node
