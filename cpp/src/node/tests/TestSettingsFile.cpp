#include <gtest/gtest.h>
#include <cstddef>
#include <filesystem>
#include <fstream>
#include <ios>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

#include "node/SettingsFile.hpp"

using node::SettingsFile;

namespace {

// Settings laid out as the Python node's libranet.yaml lays them out, and as
// SettingsFile writes them.
constexpr auto example = "local:\n"
                         "  hidden:\n"
                         "    - .git\n"
                         "    - node_modules\n"
                         "network:\n"
                         "  listen_port: 8086\n"
                         "storage:\n"
                         "  data_dir: /var/lib/libranet\n"
                         "  max_storage_bytes: 5000000";

// Whether SettingsFile::parse() turns down text as invalid.
bool refused(const std::string& text) {
    try {
        (void)SettingsFile::parse(text);
    } catch (const std::invalid_argument&) {
        return true;
    }

    return false;
}

// A YAML list of count values, written in flow style, as "[x, x, x]".
std::string list_of(std::size_t count) {
    std::string list = "[";

    for (std::size_t index = 0; index < count; ++index) {
        list += 0 == index ? "x" : ", x";
    }

    return list + "]";
}

// A file of the running test's own, under the system's directory for
// temporary files, holding text, and removed when this is destroyed. Its
// name holds the test's and a random number, so that two runs of the tests
// at once do not share one.
class ScratchFile {
public:
    explicit ScratchFile(std::string_view text);
    ~ScratchFile();
    ScratchFile(const ScratchFile&) = delete;
    ScratchFile& operator=(const ScratchFile&) = delete;
    ScratchFile(ScratchFile&&) = delete;
    ScratchFile& operator=(ScratchFile&&) = delete;

    [[nodiscard]] const std::filesystem::path& path() const noexcept;

private:
    std::filesystem::path m_path;
};

ScratchFile::ScratchFile(std::string_view text) {
    const auto* const test = testing::UnitTest::GetInstance()->current_test_info();
    std::random_device random;

    m_path = std::filesystem::temp_directory_path()
             / (std::string("libranet-") + test->test_suite_name() + "-" + test->name() + "-"
                + std::to_string(random()) + ".yaml");

    std::ofstream file(m_path, std::ios::binary | std::ios::trunc);

    file << text;
}

ScratchFile::~ScratchFile() {
    std::error_code error;

    std::filesystem::remove(m_path, error);
}

const std::filesystem::path& ScratchFile::path() const noexcept {
    return m_path;
}

// What SettingsFile::load() says when it turns the file at path down as
// invalid, or nothing if it does not.
std::string load_error(const std::filesystem::path& path) {
    try {
        (void)SettingsFile::load(path);
    } catch (const std::invalid_argument& error) {
        return error.what();
    }

    return "";
}

} // namespace

TEST(SettingsFileTest, EmptyTextHasNoSettings) {
    EXPECT_TRUE(SettingsFile::parse("").names().empty());
    EXPECT_TRUE(SettingsFile::parse("# Nothing is set yet\n").names().empty());
    EXPECT_EQ(SettingsFile::parse(""), SettingsFile());
}

TEST(SettingsFileTest, NamedBySectionAndSetting) {
    const auto settings = SettingsFile::parse(example);

    EXPECT_EQ(settings.names(),
              (std::vector<std::string>{"local.hidden", "network.listen_port", "storage.data_dir",
                                        "storage.max_storage_bytes"}));
    EXPECT_EQ(settings.value("network.listen_port"), "8086");
    EXPECT_EQ(settings.value("storage.data_dir"), "/var/lib/libranet");
    EXPECT_EQ(settings.list("local.hidden"), (SettingsFile::List{".git", "node_modules"}));
}

TEST(SettingsFileTest, ValuesKeepTheirText) {
    const auto settings = SettingsFile::parse("a:\n"
                                              "  port: \"8086\"\n"
                                              "  quote: 'it''s'\n"
                                              "  empty: \"\"\n"
                                              "  word: \"null\"\n"
                                              "  flow: [x, \"y z\"]\n"
                                              "  none: []\n");

    EXPECT_EQ(settings.value("a.port"), "8086");
    EXPECT_EQ(settings.value("a.quote"), "it's");
    EXPECT_EQ(settings.value("a.empty"), "");
    EXPECT_EQ(settings.value("a.word"), "null");
    EXPECT_EQ(settings.list("a.flow"), (SettingsFile::List{"x", "y z"}));
    EXPECT_TRUE(settings.list("a.none").empty());
}

TEST(SettingsFileTest, MissingSettingIsOutOfRange) {
    const auto settings = SettingsFile::parse(example);

    EXPECT_TRUE(settings.has("network.listen_port"));
    EXPECT_FALSE(settings.has("network.nothing"));
    EXPECT_FALSE(settings.has("network"));
    EXPECT_THROW((void)settings.value("network.nothing"), std::out_of_range);
    EXPECT_THROW((void)settings.list("network"), std::out_of_range);
}

TEST(SettingsFileTest, ValueAndListAreToldApart) {
    const auto settings = SettingsFile::parse(example);

    EXPECT_THROW((void)settings.value("local.hidden"), std::domain_error);
    EXPECT_THROW((void)settings.list("network.listen_port"), std::domain_error);
}

TEST(SettingsFileTest, TextThatIsNotYamlIsRefused) {
    EXPECT_TRUE(refused("a: [1, 2\n"));
    EXPECT_TRUE(refused("a: b: c\n"));
    EXPECT_TRUE(refused("a:\n  - b\n  c: d\n"));
    EXPECT_TRUE(refused("a:\n  b: *undefined\n"));
}

TEST(SettingsFileTest, MoreThanOneDocumentIsRefused) {
    EXPECT_TRUE(refused("a:\n  b: 1\n---\nc:\n  d: 2\n"));
}

TEST(SettingsFileTest, OnlySectionsOfSettingsAreRead) {
    EXPECT_TRUE(refused("hello\n"));
    EXPECT_TRUE(refused("- a\n- b\n"));
    EXPECT_TRUE(refused("a: 1\n"));
    EXPECT_TRUE(refused("a:\n"));
    EXPECT_TRUE(refused("a: [b]\n"));
    EXPECT_TRUE(refused("a:\n  b:\n"));
    EXPECT_TRUE(refused("a:\n  b:\n    c: 1\n"));
    EXPECT_TRUE(refused("a:\n  b: [[c]]\n"));
    EXPECT_TRUE(refused("a:\n  b: [{c: 1}]\n"));
}

TEST(SettingsFileTest, NamesMustBeTextWithoutDots) {
    EXPECT_TRUE(refused("a.b:\n  c: 1\n"));
    EXPECT_TRUE(refused("a:\n  b.c: 1\n"));
    EXPECT_TRUE(refused("\"\":\n  b: 1\n"));
    EXPECT_TRUE(refused("a:\n  \"\": 1\n"));
    EXPECT_TRUE(refused("~:\n  b: 1\n"));
    EXPECT_TRUE(refused("? [a]\n: {b: 1}\n"));
    EXPECT_TRUE(refused("a:\n  ? {b: 1}\n  : c\n"));
}

TEST(SettingsFileTest, NameGivenTwiceIsRefused) {
    EXPECT_TRUE(refused("a:\n  b: 1\na:\n  c: 2\n"));
    EXPECT_TRUE(refused("a: {}\na:\n  b: 1\n"));
    EXPECT_TRUE(refused("a:\n  b: 1\n  b: 2\n"));
}

TEST(SettingsFileTest, TextInUtf8IsRead) {
    const auto settings = SettingsFile::parse("a:\n"
                                              "  folder: M\xC3\xBAsica\n"
                                              "  notes: [\xE2\x99\xAA, \xF0\x9F\x8E\xB5]\n"
                                              "  highest: \xF4\x8F\xBF\xBF\n");

    EXPECT_EQ(settings.value("a.folder"), "M\xC3\xBAsica");
    EXPECT_EQ(settings.list("a.notes"), (SettingsFile::List{"\xE2\x99\xAA", "\xF0\x9F\x8E\xB5"}));
    EXPECT_EQ(SettingsFile::parse(settings.to_string()), settings);
}

TEST(SettingsFileTest, TextNotInUtf8IsRefused) {
    // A byte that starts no character, as the fuzzer found.
    EXPECT_TRUE(refused("a:\n  b: /var\x90\n"));
    // A character cut short, in a name.
    EXPECT_TRUE(refused("a\xC3:\n  b: 1\n"));
    // A character written longer than it need be.
    EXPECT_TRUE(refused("a:\n  b: \xC0\xAF\n"));
    // A surrogate, in a list.
    EXPECT_TRUE(refused("a:\n  b: [\xED\xA0\x80]\n"));
    // Past U+10FFFF.
    EXPECT_TRUE(refused("a:\n  b: \xF4\x90\x80\x80\n"));
}

TEST(SettingsFileTest, TooManyValuesAreRefused) {
    constexpr auto most = SettingsFile::max_values;

    EXPECT_FALSE(refused("a:\n  b: " + list_of(most) + "\n"));
    EXPECT_TRUE(refused("a:\n  b: " + list_of(most + 1) + "\n"));
    // An alias repeats a list, so the settings grow faster than the text.
    EXPECT_TRUE(refused("a:\n  b: &many " + list_of((most / 2) + 1) + "\n  c: *many\n"));
}

TEST(SettingsFileTest, WritesWhatItReads) {
    EXPECT_EQ(SettingsFile::parse(example).to_string(), example);
}

TEST(SettingsFileTest, TextThatNeedsQuotingReadsBack) {
    const auto settings = SettingsFile::parse("a:\n"
                                              "  empty: \"\"\n"
                                              "  spaced: \" x \"\n"
                                              "  word: \"null\"\n"
                                              "  colon: \"b: c\"\n"
                                              "  lines: \"one\\ntwo\"\n"
                                              "  comment: \"#x\"\n"
                                              "  list: [\"\", \"- y\", \"~\"]\n");
    const auto reread = SettingsFile::parse(settings.to_string());

    EXPECT_EQ(reread, settings);
    EXPECT_EQ(reread.value("a.lines"), "one\ntwo");
}

TEST(SettingsFileTest, NoSettingsWriteAsAnEmptyMapping) {
    EXPECT_EQ(SettingsFile().to_string(), "{}");
    EXPECT_EQ(SettingsFile::parse("{}"), SettingsFile());
}

TEST(SettingsFileTest, EqualOnlyWithTheSameSettings) {
    EXPECT_EQ(SettingsFile::parse(example), SettingsFile::parse(example));
    EXPECT_NE(SettingsFile::parse(example), SettingsFile());
    EXPECT_NE(SettingsFile::parse("a:\n  b: 1\n"), SettingsFile::parse("a:\n  b: [1]\n"));
}

TEST(SettingsFileTest, LoadsAFile) {
    const ScratchFile file(example);

    EXPECT_EQ(SettingsFile::load(file.path()), SettingsFile::parse(example));
}

TEST(SettingsFileTest, FileThatCannotBeReadIsRefused) {
    const ScratchFile file(example);

    EXPECT_THROW((void)SettingsFile::load(file.path().string() + ".missing"), std::runtime_error);
    EXPECT_THROW((void)SettingsFile::load(file.path().parent_path()), std::runtime_error);
}

TEST(SettingsFileTest, FileThatIsNotSettingsIsRefusedByName) {
    const ScratchFile file("a: 1\n");
    const auto error = load_error(file.path());

    EXPECT_NE(error.find(file.path().string()), std::string::npos);
    EXPECT_NE(error.find("A section must be a mapping of settings: a"), std::string::npos);
}
