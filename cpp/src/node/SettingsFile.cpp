#include "SettingsFile.hpp"

#include <algorithm>
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
#include "types/Converter.hpp"

using io::FileReader;
using node::SettingsFile;
using std::domain_error;
using std::invalid_argument;
using std::map;
using std::out_of_range;
using std::runtime_error;
using std::set;
using std::size_t;
using std::string;
using std::string_view;
using std::vector;
using std::filesystem::path;
using types::Converter;

namespace {

using List = SettingsFile::List;
using Value = SettingsFile::Value;
using Settings = map<string, Value>;

// The text of a scalar. yaml-cpp keeps bytes that are not UTF-8 as they are
// read, but writes them as U+FFFD, so such text would not be written back as
// it was read.
string text_of(const YAML::Node& scalar) {
    if (!Converter::is_utf8(scalar.Scalar())) {
        throw invalid_argument("Settings must be text in UTF-8");
    }

    return scalar.Scalar();
}

// The text of file.
string read_file(const path& file) {
    try {
        return FileReader(file).read_all();
    } catch (const runtime_error&) {
        throw runtime_error("Cannot read the settings file: " + file.string());
    }
}

// The one document text holds, or a null node if it holds none. Its type,
// YAML::Node, is deduced: CodeQL reads the code without yaml-cpp's headers,
// and takes a declaration that starts with YAML::Node for a variable named
// YAML.
auto only_document(string_view text) {
    vector<YAML::Node> documents;

    try {
        documents = YAML::LoadAll(string(text));
    } catch (const YAML::Exception& error) {
        throw invalid_argument(string("Settings are not YAML: ") + error.what());
    }

    if (documents.size() > 1) {
        throw invalid_argument("Settings must be one YAML document");
    }

    return documents.empty() ? YAML::Node() : documents.front();
}

// The name a key gives a section or a setting. A dot would make a setting's
// name read as if it were in another section.
string name_of(const YAML::Node& key) {
    if (!key.IsScalar()) {
        throw invalid_argument("A section or setting must be named with text");
    }

    auto name = text_of(key);

    if (name.empty()) {
        throw invalid_argument("A section or setting must not have an empty name");
    }

    if (string::npos != name.find('.')) {
        throw invalid_argument("A section's or setting's name must not hold a dot: " + name);
    }

    return name;
}

// The value, or the list of values, a setting gives.
Value value_of(const YAML::Node& setting, const string& name) {
    if (setting.IsScalar()) {
        return text_of(setting);
    }

    if (!setting.IsSequence()) {
        throw invalid_argument("A setting must be a value or a list of values: " + name);
    }

    List list;

    for (const auto& item : setting) {
        if (!item.IsScalar()) {
            throw invalid_argument("A setting's list must hold only values: " + name);
        }

        list.push_back(text_of(item));
    }

    return list;
}

// How many values a setting gives: one, or each in its list.
size_t count_of(const Value& value) {
    const auto* list = std::get_if<List>(&value);

    return nullptr == list ? 1 : list->size();
}

// Writes a setting's value, or its list, as YAML. The emitter comes after the
// value: CodeQL reads the code without yaml-cpp's headers, and takes a
// declaration whose first parameter starts with YAML:: for a variable.
void write_value(const Value& value, YAML::Emitter& out) {
    if (const auto* text = std::get_if<string>(&value); nullptr != text) {
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
    set<string> m_sections;
    size_t m_values{0};

    void _add(const string& name, const YAML::Node& setting);
};

void SectionReader::read(const YAML::Node& key, const YAML::Node& section) {
    const auto section_name = name_of(key);

    if (!m_sections.insert(section_name).second) {
        throw invalid_argument("A section is given twice: " + section_name);
    }

    if (!section.IsMap()) {
        throw invalid_argument("A section must be a mapping of settings: " + section_name);
    }

    for (const auto& setting : section) {
        _add(section_name + "." + name_of(setting.first), setting.second);
    }
}

const Settings& SectionReader::settings() const noexcept {
    return m_settings;
}

void SectionReader::_add(const string& name, const YAML::Node& setting) {
    const auto [entry, is_new] = m_settings.try_emplace(name, value_of(setting, name));

    if (!is_new) {
        throw invalid_argument("A setting is given twice: " + name);
    }

    m_values += count_of(entry->second);

    if (m_values > SettingsFile::max_values) {
        throw invalid_argument("Settings must hold no more than "
                               + std::to_string(SettingsFile::max_values) + " values");
    }
}

// The value of the setting in settings named name. Throws std::out_of_range
// when there is none.
const Value& setting_at(const Settings& settings, const string& name) {
    const auto found = settings.find(name);

    if (found == settings.end()) {
        throw out_of_range("No such setting: " + name);
    }

    return found->second;
}

} // namespace

namespace node {

SettingsFile SettingsFile::parse(string_view text) {
    const auto document = only_document(text);

    if (document.IsNull()) {
        return {};
    }

    if (!document.IsMap()) {
        throw invalid_argument("Settings must be a mapping of sections");
    }

    SectionReader reader{};

    for (const auto& section : document) {
        reader.read(section.first, section.second);
    }

    return SettingsFile(reader.settings());
}

SettingsFile SettingsFile::load(const path& file) {
    const auto text = read_file(file);

    try {
        return parse(text);
    } catch (const invalid_argument& error) {
        throw invalid_argument("Cannot read the settings in " + file.string() + ": "
                               + error.what());
    }
}

SettingsFile::SettingsFile(map<string, Value> settings)
    : m_settings(std::move(settings)) {}

vector<string> SettingsFile::names() const {
    vector<string> result;

    result.reserve(m_settings.size());
    std::transform(m_settings.begin(), m_settings.end(), std::back_inserter(result),
                   [](const auto& setting) { return setting.first; });
    return result;
}

bool SettingsFile::has(const string& name) const {
    return m_settings.find(name) != m_settings.end();
}

const string& SettingsFile::value(const string& name) const {
    const auto* text = std::get_if<string>(&setting_at(m_settings, name));

    if (nullptr == text) {
        throw domain_error("Setting is a list, not a value: " + name);
    }

    return *text;
}

const SettingsFile::List& SettingsFile::list(const string& name) const {
    const auto* list = std::get_if<List>(&setting_at(m_settings, name));

    if (nullptr == list) {
        throw domain_error("Setting is a value, not a list: " + name);
    }

    return *list;
}

// Each section is written once, with all its settings: their names are in
// order, so those of a section, which start alike, come together.
string SettingsFile::to_string() const {
    YAML::Emitter out;
    string section; // The one being written

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
