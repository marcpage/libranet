#include "SettingsFile.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <filesystem>
#include <iterator>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <variant>
#include <vector>

#include <yaml-cpp/emitter.h>
#include <yaml-cpp/emittermanip.h>
#include <yaml-cpp/exceptions.h>
#include <yaml-cpp/node/impl.h> // IWYU pragma: keep
#include <yaml-cpp/node/iterator.h>
#include <yaml-cpp/node/node.h>
#include <yaml-cpp/node/parse.h>

#include "io/FileReader.hpp"

namespace {

using List = node::SettingsFile::List;
using Value = node::SettingsFile::Value;
using Settings = std::map<std::string, Value>;

// The bytes a character in UTF-8 may start with, first_low to first_high;
// those its second byte may be, second_low to second_high; and how many bytes
// it takes. The second bytes leave out a character written longer than it
// need be, one past U+10FFFF, and the surrogates UTF-16 takes: RFC 3629 §4.
struct Utf8Start {
    unsigned char first_low;
    unsigned char first_high;
    unsigned char second_low;
    unsigned char second_high;
    std::size_t length;
};

constexpr std::array utf8_starts{
    Utf8Start{0x00U, 0x7FU, 0x00U, 0x00U, 1},
    Utf8Start{0xC2U, 0xDFU, 0x80U, 0xBFU, 2},
    Utf8Start{0xE0U, 0xE0U, 0xA0U, 0xBFU, 3},
    Utf8Start{0xE1U, 0xECU, 0x80U, 0xBFU, 3},
    Utf8Start{0xEDU, 0xEDU, 0x80U, 0x9FU, 3},
    Utf8Start{0xEEU, 0xEFU, 0x80U, 0xBFU, 3},
    Utf8Start{0xF0U, 0xF0U, 0x90U, 0xBFU, 4},
    Utf8Start{0xF1U, 0xF3U, 0x80U, 0xBFU, 4},
    Utf8Start{0xF4U, 0xF4U, 0x80U, 0x8FU, 4},
};

// What each byte of a character in UTF-8 after its second may be.
constexpr unsigned char utf8_tail_low = 0x80U;
constexpr unsigned char utf8_tail_high = 0xBFU;

// Whether text holds the bytes after the first of the character it starts
// with, which start describes.
bool has_rest_of(std::string_view text, const Utf8Start& start) {
    if (text.size() < start.length) {
        return false;
    }

    for (std::size_t index = 1; index < start.length; ++index) {
        const auto byte = static_cast<unsigned char>(text.at(index));
        const auto low = 1 == index ? start.second_low : utf8_tail_low;
        const auto high = 1 == index ? start.second_high : utf8_tail_high;

        if (byte < low || high < byte) {
            return false;
        }
    }

    return true;
}

// How many bytes the character in UTF-8 that text starts with takes, or 0 if
// it starts with none.
std::size_t utf8_length(std::string_view text) {
    const auto first = static_cast<unsigned char>(text.at(0));
    const auto* const start = std::find_if(
        utf8_starts.begin(), utf8_starts.end(), [first](const Utf8Start& candidate) {
        return candidate.first_low <= first && first <= candidate.first_high;
    });

    if (utf8_starts.end() == start) {
        return 0;
    }

    return has_rest_of(text, *start) ? start->length : 0;
}

// The text of a scalar. yaml-cpp keeps bytes that are not UTF-8 as they are
// read, but writes them as U+FFFD, so such text would not be written back as
// it was read.
std::string text_of(const YAML::Node& scalar) {
    std::string_view rest = scalar.Scalar();

    while (!rest.empty()) {
        const auto length = utf8_length(rest);

        if (0 == length) {
            throw std::invalid_argument("Settings must be text in UTF-8");
        }

        rest.remove_prefix(length);
    }

    return scalar.Scalar();
}

// The text of the file at path.
std::string read_file(const std::filesystem::path& path) {
    try {
        return io::FileReader(path).read_all();
    } catch (const std::runtime_error&) {
        throw std::runtime_error("Cannot read the settings file: " + path.string());
    }
}

// The one document text holds, or a null node if it holds none. Its type,
// YAML::Node, is deduced: CodeQL reads the code without yaml-cpp's headers,
// and takes a declaration that starts with YAML::Node for a variable named
// YAML.
auto only_document(std::string_view text) {
    std::vector<YAML::Node> documents;

    try {
        documents = YAML::LoadAll(std::string(text));
    } catch (const YAML::Exception& error) {
        throw std::invalid_argument(std::string("Settings are not YAML: ") + error.what());
    }

    if (documents.size() > 1) {
        throw std::invalid_argument("Settings must be one YAML document");
    }

    return documents.empty() ? YAML::Node() : documents.front();
}

// The name a key gives a section or a setting. A dot would make a setting's
// name read as if it were in another section.
std::string name_of(const YAML::Node& key) {
    if (!key.IsScalar()) {
        throw std::invalid_argument("A section or setting must be named with text");
    }

    auto name = text_of(key);

    if (name.empty()) {
        throw std::invalid_argument("A section or setting must not have an empty name");
    }

    if (std::string::npos != name.find('.')) {
        throw std::invalid_argument("A section's or setting's name must not hold a dot: " + name);
    }

    return name;
}

// The value, or the list of values, a setting gives.
Value value_of(const YAML::Node& setting, const std::string& name) {
    if (setting.IsScalar()) {
        return text_of(setting);
    }

    if (!setting.IsSequence()) {
        throw std::invalid_argument("A setting must be a value or a list of values: " + name);
    }

    List list;

    for (const auto& item : setting) {
        if (!item.IsScalar()) {
            throw std::invalid_argument("A setting's list must hold only values: " + name);
        }

        list.push_back(text_of(item));
    }

    return list;
}

// How many values a setting gives: one, or each in its list.
std::size_t count_of(const Value& value) {
    const auto* list = std::get_if<List>(&value);

    return nullptr == list ? 1 : list->size();
}

// Writes a setting's value, or its list, as YAML. The emitter comes after the
// value: CodeQL reads the code without yaml-cpp's headers, and takes a
// declaration whose first parameter starts with YAML:: for a variable.
void write_value(const Value& value, YAML::Emitter& out) {
    if (const auto* text = std::get_if<std::string>(&value); nullptr != text) {
        out << *text;
        return;
    }

    out << YAML::BeginSeq;

    for (const auto& item : std::get<List>(value)) {
        out << item;
    }

    out << YAML::EndSeq;
}

// Reads the sections of a document into settings, each named
// "section.setting", turning down what a settings file must not hold.
class SectionReader {
public:
    void read(const YAML::Node& key, const YAML::Node& section);
    [[nodiscard]] const Settings& settings() const noexcept;

private:
    Settings m_settings;
    std::set<std::string> m_sections;
    std::size_t m_values{0};

    void _add(const std::string& name, const YAML::Node& setting);
};

void SectionReader::read(const YAML::Node& key, const YAML::Node& section) {
    const auto section_name = name_of(key);

    if (!m_sections.insert(section_name).second) {
        throw std::invalid_argument("A section is given twice: " + section_name);
    }

    if (!section.IsMap()) {
        throw std::invalid_argument("A section must be a mapping of settings: " + section_name);
    }

    for (const auto& setting : section) {
        _add(section_name + "." + name_of(setting.first), setting.second);
    }
}

const Settings& SectionReader::settings() const noexcept {
    return m_settings;
}

void SectionReader::_add(const std::string& name, const YAML::Node& setting) {
    const auto [entry, is_new] = m_settings.try_emplace(name, value_of(setting, name));

    if (!is_new) {
        throw std::invalid_argument("A setting is given twice: " + name);
    }

    m_values += count_of(entry->second);

    if (m_values > node::SettingsFile::max_values) {
        throw std::invalid_argument("Settings must hold no more than "
                                    + std::to_string(node::SettingsFile::max_values) + " values");
    }
}

// The value of the setting in settings named name. Throws std::out_of_range
// when there is none.
const Value& setting_at(const Settings& settings, const std::string& name) {
    const auto found = settings.find(name);

    if (found == settings.end()) {
        throw std::out_of_range("No such setting: " + name);
    }

    return found->second;
}

} // namespace

namespace node {

SettingsFile SettingsFile::parse(std::string_view text) {
    const auto document = only_document(text);

    if (document.IsNull()) {
        return {};
    }

    if (!document.IsMap()) {
        throw std::invalid_argument("Settings must be a mapping of sections");
    }

    SectionReader reader{};

    for (const auto& section : document) {
        reader.read(section.first, section.second);
    }

    return SettingsFile(reader.settings());
}

SettingsFile SettingsFile::load(const std::filesystem::path& path) {
    const auto text = read_file(path);

    try {
        return parse(text);
    } catch (const std::invalid_argument& error) {
        throw std::invalid_argument("Cannot read the settings in " + path.string() + ": "
                                    + error.what());
    }
}

SettingsFile::SettingsFile(std::map<std::string, Value> settings)
    : m_settings(std::move(settings)) {}

std::vector<std::string> SettingsFile::names() const {
    std::vector<std::string> result;

    result.reserve(m_settings.size());
    std::transform(m_settings.begin(), m_settings.end(), std::back_inserter(result),
                   [](const auto& setting) { return setting.first; });
    return result;
}

bool SettingsFile::has(const std::string& name) const {
    return m_settings.find(name) != m_settings.end();
}

const std::string& SettingsFile::value(const std::string& name) const {
    const auto* text = std::get_if<std::string>(&setting_at(m_settings, name));

    if (nullptr == text) {
        throw std::domain_error("Setting is a list, not a value: " + name);
    }

    return *text;
}

const SettingsFile::List& SettingsFile::list(const std::string& name) const {
    const auto* list = std::get_if<List>(&setting_at(m_settings, name));

    if (nullptr == list) {
        throw std::domain_error("Setting is a value, not a list: " + name);
    }

    return *list;
}

// Each section is written once, with all its settings: their names are in
// order, so those of a section, which start alike, come together.
std::string SettingsFile::to_string() const {
    YAML::Emitter out;
    std::string section; // The one being written

    out << YAML::BeginMap;

    for (const auto& [name, setting] : m_settings) {
        const auto dot = name.find('.');

        if (name.compare(0, dot, section) != 0) {
            if (!section.empty()) {
                out << YAML::EndMap;
            }

            section = name.substr(0, dot);
            out << YAML::Key << section << YAML::Value << YAML::BeginMap;
        }

        out << YAML::Key << name.substr(dot + 1) << YAML::Value;
        write_value(setting, out);
    }

    if (!section.empty()) {
        out << YAML::EndMap;
    }

    out << YAML::EndMap;
    return {out.c_str(), out.size()};
}

bool operator==(const SettingsFile& left, const SettingsFile& right) {
    return left.m_settings == right.m_settings;
}

bool operator!=(const SettingsFile& left, const SettingsFile& right) {
    return !(left == right);
}

} // namespace node
