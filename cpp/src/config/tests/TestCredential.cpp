#include <gtest/gtest.h>
#include <algorithm>
#include <cstddef>
#include <filesystem>
#include <fstream>
#include <future>
#include <ios>
#include <iterator>
#include <memory>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#include "config/Credential.hpp"
#include "logger/ILogOutput.hpp"
#include "logger/Logger.hpp"

using config::Credential;
using logger::ILogOutput;
using logger::Logger;
using std::runtime_error;
using std::shared_ptr;
using std::string;
using std::string_view;
using std::vector;
using std::filesystem::path;
using std::filesystem::perms;

namespace {

constexpr auto admin = "admin:secret";

// A hash Python's cryptography package made of admin, with
// Argon2id(salt=b"libranet-salt-16", length=32, iterations=1, lanes=1,
// memory_cost=8).derive_phc_encoded(), at costs far below the ones a
// credential is captured at.
constexpr auto python_hash = "$argon2id$v=19$m=8,t=1,p=1$bGlicmFuZXQtc2FsdC0xNg"
                             "$j1lm18Hvt9faS3JaxL2KIrTxqXhHa43cIRmsKhvgB3w";

// How many requests race to capture a credential.
constexpr std::size_t racers = 6;

// What only its owner may do with the credential's file.
constexpr auto owner_only = perms::owner_read | perms::owner_write;

// What the file at file_path holds.
string read_file(const path& file_path) {
    std::ifstream file(file_path, std::ios::binary);

    return {std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
}

// The file at file_path, with text in it.
void write_file(const path& file_path, string_view text) {
    std::ofstream file(file_path, std::ios::binary | std::ios::trunc);

    file << text;
}

// The names of what is in directory, in order.
vector<string> names_in(const path& directory) {
    vector<string> names;

    std::transform(std::filesystem::directory_iterator(directory),
                   std::filesystem::directory_iterator(), std::back_inserter(names),
                   [](const std::filesystem::directory_entry& entry) {
        return entry.path().filename().string();
    });
    std::sort(names.begin(), names.end());
    return names;
}

// Whether credential takes each of given, each asked from a thread of its
// own, all at once.
vector<bool> race(const Credential& credential, const vector<string>& given) {
    std::promise<void> start;
    const auto started = start.get_future().share();
    vector<std::future<bool>> results;
    vector<bool> taken;

    std::transform(given.begin(), given.end(), std::back_inserter(results),
                   [&credential, started](const string& credentials) {
        return std::async(std::launch::async, [&credential, &credentials, started] {
            started.wait();
            return credential.authenticate(credentials);
        });
    });
    start.set_value();
    std::transform(results.begin(), results.end(), std::back_inserter(taken),
                   [](std::future<bool>& result) { return result.get(); });
    return taken;
}

// A directory of the running test's own, made under the system's directory
// for temporary files, and removed with all it holds when this is destroyed.
// Its name holds the test's and a random number, so that two runs of the
// tests at once do not share one.
class ScratchDirectory {
public:
    ScratchDirectory();
    ~ScratchDirectory();

    [[nodiscard]] const path& directory() const noexcept;

    ScratchDirectory(const ScratchDirectory&) = delete;
    ScratchDirectory& operator=(const ScratchDirectory&) = delete;
    ScratchDirectory(ScratchDirectory&&) = delete;
    ScratchDirectory& operator=(ScratchDirectory&&) = delete;

private:
    path m_path;
};

ScratchDirectory::ScratchDirectory() {
    const auto* const test = testing::UnitTest::GetInstance()->current_test_info();
    std::random_device random;

    m_path = std::filesystem::temp_directory_path()
             / (string("libranet-") + test->test_suite_name() + "-" + test->name() + "-"
                + std::to_string(random()));
    std::filesystem::create_directories(m_path);
}

ScratchDirectory::~ScratchDirectory() {
    std::error_code error;

    std::filesystem::remove_all(m_path, error);
}

const path& ScratchDirectory::directory() const noexcept {
    return m_path;
}

// Keeps each line it is given, where a test can read them while the Logger
// owns it.
class KeepingOutput : public ILogOutput {
public:
    explicit KeepingOutput(shared_ptr<vector<string>> lines)
        : m_lines(std::move(lines)) {}

    void log(string_view line) override {
        m_lines->emplace_back(line);
    }

private:
    shared_ptr<vector<string>> m_lines;
};

// Each test has a data directory for the credential, and keeps what is
// logged.
class CredentialTest : public testing::Test {
protected:
    void SetUp() override {
        Logger::remove_all_outputs();
        Logger::add_output(std::make_unique<KeepingOutput>(m_lines));
        Logger::reset_levels(Logger::Level::Info);
    }

    void TearDown() override {
        Logger::remove_all_outputs();
    }

    [[nodiscard]] const path& directory() const noexcept {
        return m_directory.directory();
    }

    [[nodiscard]] path file() const {
        return directory() / "config-credential";
    }

    [[nodiscard]] const Credential& credential() const noexcept {
        return m_credential;
    }

    // Whether a line logged holds text.
    [[nodiscard]] bool logged(string_view text) const {
        return std::any_of(m_lines->begin(), m_lines->end(), [text](const string& line) {
            return string::npos != line.find(text);
        });
    }

private:
    ScratchDirectory m_directory;
    Credential m_credential{m_directory.directory()};
    shared_ptr<vector<string>> m_lines = std::make_shared<vector<string>>();
};

} // namespace

TEST_F(CredentialTest, IsKeptInTheDataDirectory) {
    EXPECT_EQ(credential().file(), file());
}

TEST_F(CredentialTest, CapturesTheFirstCredentialGiven) {
    EXPECT_TRUE(credential().authenticate(admin));

    EXPECT_TRUE(std::filesystem::exists(file()));
    EXPECT_TRUE(logged("Captured the /config credential: " + file().string()));
}

TEST_F(CredentialTest, ChecksLaterCredentialsAgainstTheOneCaptured) {
    ASSERT_TRUE(credential().authenticate(admin));

    EXPECT_TRUE(credential().authenticate(admin));
    EXPECT_FALSE(credential().authenticate("admin:other"));
    EXPECT_FALSE(credential().authenticate("other:secret"));
    EXPECT_FALSE(credential().authenticate(""));
}

// The username is hashed with the password, so that the file names nobody.
TEST_F(CredentialTest, KeepsOnlyAnArgon2idHash) {
    ASSERT_TRUE(credential().authenticate(admin));

    const auto kept = read_file(file());

    EXPECT_EQ(kept.rfind("$argon2id$v=19$m=19456,t=2,p=1$", 0), 0U);
    EXPECT_EQ(kept.find("admin"), string::npos);
    EXPECT_EQ(kept.find("secret"), string::npos);
}

TEST_F(CredentialTest, HashesUnderAFreshSaltEachTime) {
    ASSERT_TRUE(credential().authenticate(admin));
    const auto first = read_file(file());

    (void)std::filesystem::remove(file());
    ASSERT_TRUE(credential().authenticate(admin));

    EXPECT_NE(read_file(file()), first);
}

TEST_F(CredentialTest, OnlyItsOwnerMayReadIt) {
    ASSERT_TRUE(credential().authenticate(admin));

    EXPECT_EQ(std::filesystem::status(file()).permissions(), owner_only);
}

// The hash is written to a file of its own first, which is gone once it is
// linked into place.
TEST_F(CredentialTest, LeavesNothingBesideItsFile) {
    ASSERT_TRUE(credential().authenticate(admin));

    EXPECT_EQ(names_in(directory()), vector<string>{"config-credential"});
}

TEST_F(CredentialTest, MakesTheDataDirectory) {
    const Credential nested(directory() / "data" / "libranet");

    EXPECT_TRUE(nested.authenticate(admin));

    EXPECT_TRUE(std::filesystem::exists(directory() / "data" / "libranet" / "config-credential"));
}

// It is read on every check, so removing it takes effect at once.
TEST_F(CredentialTest, RemovingItLetsTheNextOneGivenBeCaptured) {
    ASSERT_TRUE(credential().authenticate(admin));
    (void)std::filesystem::remove(file());

    EXPECT_TRUE(credential().authenticate("admin:other"));
    EXPECT_FALSE(credential().authenticate(admin));
    EXPECT_TRUE(credential().authenticate("admin:other"));
}

// A hash is checked at the costs it names, so that they can be raised
// without stranding a credential captured under the old ones.
TEST_F(CredentialTest, ChecksAHashAtTheCostsItNames) {
    write_file(file(), python_hash);

    EXPECT_TRUE(credential().authenticate(admin));
    EXPECT_FALSE(credential().authenticate("admin:other"));
    EXPECT_EQ(read_file(file()), python_hash);
}

TEST_F(CredentialTest, AFileHoldingNoHashMatchesNothing) {
    write_file(file(), "admin:secret");

    EXPECT_FALSE(credential().authenticate(admin));
    EXPECT_EQ(read_file(file()), "admin:secret");
}

TEST_F(CredentialTest, AFileFarLargerThanAHashIsRefused) {
    constexpr std::size_t large = 64UL * 1024UL;

    write_file(file(), string(large, '$'));

    EXPECT_THROW((void)credential().authenticate(admin), runtime_error);
}

// Of two requests capturing at once, whichever makes the file first wins,
// and the others are checked against it (HTTP API §2.3.1).
TEST_F(CredentialTest, OfRequestsRacingToCaptureOneWins) {
    vector<string> given;

    given.reserve(racers);

    for (std::size_t index = 0; index < racers; ++index) {
        given.push_back("user" + std::to_string(index) + ":password");
    }

    const auto taken = race(credential(), given);
    const auto winner = std::find(taken.begin(), taken.end(), true);

    EXPECT_EQ(std::count(taken.begin(), taken.end(), true), 1);
    ASSERT_NE(winner, taken.end());
    EXPECT_TRUE(credential().authenticate(given.at(
        static_cast<std::size_t>(std::distance(taken.begin(), winner)))));
    EXPECT_EQ(names_in(directory()), vector<string>{"config-credential"});
}

TEST_F(CredentialTest, RacingRequestsGivingOneCredentialAreAllTaken) {
    const vector<string> given(racers, admin);

    const auto taken = race(credential(), given);

    EXPECT_EQ(std::count(taken.begin(), taken.end(), true), static_cast<std::ptrdiff_t>(racers));
    EXPECT_EQ(names_in(directory()), vector<string>{"config-credential"});
}
