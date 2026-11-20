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
// is: a mapping of sections, each a mapping of settings, each a value or a
// list of values. A setting is named by its section and its own name, joined
// by a dot, as "storage.max_storage_bytes", the name --set takes. Each value
// is kept as the text the file gives, for what reads it to take as the type
// it needs. Read with yaml-cpp.
class SettingsFile {
public:
    using List = std::vector<std::string>;
    using Value = std::variant<std::string, List>;

    // The most values a file may give, counting each value in a list: far
    // more than the Python node's few hundred settings, and a bound on what an
    // alias that repeats a list many times over can make it hold.
    static constexpr std::size_t max_values = 10'000;

    // Reads YAML text. A document with nothing in it has no settings. Throws
    // std::invalid_argument for text that is not one YAML document, holds a
    // name or value that is not UTF-8, or is not laid out as above: a name
    // that is not text, is empty, holds a dot, or is given twice; a setting
    // with no value, or with more than a list of values; or more than
    // max_values values.
    [[nodiscard]] static SettingsFile parse(std::string_view text);

    // Reads the file at path, as parse() reads text. Throws
    // std::runtime_error if it cannot be read, and std::invalid_argument,
    // naming the file, for what parse() refuses.
    [[nodiscard]] static SettingsFile load(const std::filesystem::path& path);

    // No settings, as an empty file gives.
    SettingsFile() = default;

    // The name of each setting, in order.
    [[nodiscard]] std::vector<std::string> names() const;
    [[nodiscard]] bool has(const std::string& name) const;

    // The value of the setting name. Throws std::out_of_range if there is no
    // such setting, and std::domain_error if it is a list.
    [[nodiscard]] const std::string& value(const std::string& name) const;

    // The list of the setting name. Throws std::out_of_range if there is no
    // such setting, and std::domain_error if it is a single value.
    [[nodiscard]] const List& list(const std::string& name) const;

    // The settings as YAML, which parse() reads back as the same settings.
    [[nodiscard]] std::string to_string() const;

    friend bool operator==(const SettingsFile& left, const SettingsFile& right);
    friend bool operator!=(const SettingsFile& left, const SettingsFile& right);

private:
    std::map<std::string, Value> m_settings;

    explicit SettingsFile(std::map<std::string, Value> settings);

    [[nodiscard]] const Value& _at(const std::string& name) const;
};

} // namespace node
