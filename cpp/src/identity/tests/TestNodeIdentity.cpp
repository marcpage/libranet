#include <gtest/gtest.h>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <ios>
#include <iterator>
#include <memory>
#include <optional>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <utility>
#include <vector>

#include "crypto/IMultiKey.hpp"
#include "crypto/ISignKey.hpp"
#include "identity/NodeIdentity.hpp"
#include "logger/ILogOutput.hpp"
#include "logger/Logger.hpp"
#include "storage/DataDir.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"
#include "types/Converter.hpp"

using crypto::IMultiKey;
using crypto::ISignKey;
using identity::NodeIdentity;
using logger::ILogOutput;
using logger::Logger;
using std::invalid_argument;
using std::runtime_error;
using std::shared_ptr;
using std::string;
using std::string_view;
using std::vector;
using std::filesystem::path;
using std::filesystem::perms;
using storage::DataDir;
using storage::HashPrefix;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;
using types::Kilobyte;

namespace {

// RFC 8032 §7.1 TEST 2's key, as Python's cryptography package writes it,
// as the Python node writes its own: the private key in PKCS #8, the public
// key in SubjectPublicKeyInfo, and the SHA-256 of that public key, from
//
//   key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(
//       "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb"))
//   public = key.public_key().public_bytes(
//       Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
//   key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
//   "sha256/" + hashlib.sha256(public).hexdigest()
constexpr auto python_private_pem =
    "-----BEGIN PRIVATE KEY-----\n"
    "MC4CAQAwBQYDK2VwBCIEIEzNCJso/5banbbDRuwRTg9bijGfNaumJNqM9u1PuKb7\n"
    "-----END PRIVATE KEY-----\n";
constexpr auto python_public_pem =
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEAPUAXw+hDiVqStwqnTRt+vJyYLM8uxJaMwM1V8Sr0Zgw=\n"
    "-----END PUBLIC KEY-----\n";
constexpr auto python_id =
    "sha256/bf019c455f05e75ce74ca02a55a4b88bab561f85a76555d8281a79f7c2985233";

// RFC 8032 §7.1 TEST 2's message, which its key signs: one byte, 0x72,
// the letter r.
constexpr string_view test_2_message = "r";

// The most a key file may hold, in bytes, and the size of an RSA key to
// refuse.
constexpr std::size_t most_key_file_size = 64 * Kilobyte;
constexpr std::size_t rsa_key_bits = 2'048;

// What only its owner may do with a key file.
constexpr auto owner_only = perms::owner_read | perms::owner_write;

// data, as lower-case hex.
string hex_of(const NodeIdentity::Data& data) {
    constexpr string_view digits = "0123456789abcdef";
    constexpr auto nibble_bits = 4U;
    constexpr auto nibble_mask = 0x0FU;
    string hex;

    for (const auto byte : data) {
        const auto value = std::to_integer<unsigned int>(byte);

        hex.push_back(digits.at(value >> nibble_bits));
        hex.push_back(digits.at(value & nibble_mask));
    }

    return hex;
}

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

// A store that says it holds everything, and fails the test when anything
// is put in it.
class HoldingEverything : public IDataSource {
public:
    [[nodiscard]] bool has([[maybe_unused]] const Identifier& identifier) const override {
        return true;
    }

    [[nodiscard]] std::optional<Data>
    get([[maybe_unused]] const Identifier& identifier) const override {
        return std::nullopt;
    }

    void put(const Identifier& identifier, [[maybe_unused]] const Data& body) const override {
        ADD_FAILURE() << "Put what is held already: " << identifier.to_string();
    }

    [[nodiscard]] bool remove([[maybe_unused]] const Identifier& identifier) const override {
        return false;
    }

    [[nodiscard]] std::uintmax_t size() const override {
        return 0;
    }

    [[nodiscard]] Identifiers
    starting_with([[maybe_unused]] const HashPrefix& prefix) const override {
        return {};
    }
};

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

// Each test has a directory for the key file and the store, and keeps what
// is logged.
class NodeIdentityTest : public testing::Test {
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

    [[nodiscard]] path key_file() const {
        return directory() / "node-key.pem";
    }

    [[nodiscard]] const DataDir& store() const noexcept {
        return m_store;
    }

    // Writes text to the key file, which only its owner may then read.
    void write_key(string_view text) const {
        write_file(key_file(), text);
        std::filesystem::permissions(key_file(), owner_only);
    }

    // Whether an identity turns down a key file holding text as holding no
    // key.
    [[nodiscard]] bool refused(string_view text) const {
        write_key(text);

        try {
            const NodeIdentity identity(key_file(), store());
        } catch (const invalid_argument&) {
            return true;
        }

        return false;
    }

    // Whether a line logged holds text.
    [[nodiscard]] bool logged(string_view text) const {
        return std::any_of(m_lines->begin(), m_lines->end(), [text](const string& line) {
            return string::npos != line.find(text);
        });
    }

private:
    ScratchDirectory m_directory;
    DataDir m_store{m_directory.directory() / "cas"};
    shared_ptr<vector<string>> m_lines = std::make_shared<vector<string>>();
};

} // namespace

TEST_F(NodeIdentityTest, MakesAKeyWhenThereIsNone) {
    const NodeIdentity identity(key_file(), store());
    const auto kept = ISignKey::from_pem(read_file(key_file()));

    EXPECT_TRUE(kept->has_private_key());
    EXPECT_EQ(kept->id(), "ed25519-key");
    EXPECT_EQ(kept->public_pem(), identity.public_pem());
    EXPECT_EQ(identity.public_pem().rfind("-----BEGIN PUBLIC KEY-----\n", 0), 0U);
    EXPECT_TRUE(logged("Made the node's key: " + key_file().string()));
}

TEST_F(NodeIdentityTest, OnlyItsOwnerMayReadTheKeyItMakes) {
    (void)NodeIdentity(key_file(), store());

    EXPECT_EQ(std::filesystem::status(key_file()).permissions(), owner_only);
}

// The key is written to a file of its own first, which is gone once it is
// linked into place.
TEST_F(NodeIdentityTest, LeavesNothingBesideTheKeyFile) {
    (void)NodeIdentity(key_file(), store());

    EXPECT_EQ(names_in(directory()), (vector<string>{"cas", "node-key.pem"}));
}

TEST_F(NodeIdentityTest, KeepsItsKeyFromOneStartToTheNext) {
    const NodeIdentity first(key_file(), store());
    const NodeIdentity second(key_file(), store());

    EXPECT_EQ(second.id().to_string(), first.id().to_string());
    EXPECT_EQ(second.public_pem(), first.public_pem());
    EXPECT_FALSE(logged("Others could read"));
}

TEST_F(NodeIdentityTest, ReadsAKeyThePythonNodeWrote) {
    write_key(python_private_pem);

    const NodeIdentity identity(key_file(), store());

    EXPECT_EQ(identity.public_pem(), python_public_pem);
    EXPECT_EQ(identity.id().to_string(), python_id);
    EXPECT_EQ(read_file(key_file()), python_private_pem);
    EXPECT_FALSE(logged("Made the node's key"));
}

TEST_F(NodeIdentityTest, IdIsTheIdentifierOfThePublicKey) {
    const NodeIdentity identity(key_file(), store());

    EXPECT_EQ(identity.id().to_string(),
              Identifier::of(Converter::bytes_of(identity.public_pem())).to_string());
}

TEST_F(NodeIdentityTest, StoresThePublicKeyUnderItsId) {
    const NodeIdentity identity(key_file(), store());
    const auto stored = store().get(identity.id()).value_or(NodeIdentity::Data{});

    EXPECT_EQ(Converter::text_of(stored), identity.public_pem());
}

TEST_F(NodeIdentityTest, LeavesAPublicKeyStoredAlready) {
    const HoldingEverything holding;

    write_key(python_private_pem);

    const NodeIdentity identity(key_file(), holding);

    EXPECT_EQ(identity.id().to_string(), python_id);
}

TEST_F(NodeIdentityTest, SignsWithItsKey) {
    write_key(python_private_pem);

    const NodeIdentity identity(key_file(), store());

    // RFC 8032 §7.1 TEST 2's signature.
    EXPECT_EQ(hex_of(identity.sign(test_2_message)),
              "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
              "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00");
}

TEST_F(NodeIdentityTest, ItsSignaturesVerifyUnderItsPublicKey) {
    constexpr string_view message = "\"@method\": PUT";
    const NodeIdentity identity(key_file(), store());
    const auto public_key = ISignKey::from_pem(identity.public_pem());

    EXPECT_FALSE(public_key->has_private_key());
    EXPECT_TRUE(public_key->verify(message, identity.sign(message)));
}

TEST_F(NodeIdentityTest, AKeyFileOthersMayReadIsMadeReadableOnlyByItsOwner) {
    write_key(python_private_pem);
    std::filesystem::permissions(key_file(), perms::group_read | perms::others_read,
                                 std::filesystem::perm_options::add);

    const NodeIdentity identity(key_file(), store());

    EXPECT_EQ(std::filesystem::status(key_file()).permissions(), owner_only);
    EXPECT_TRUE(logged("Others could read the node's key file, which only its owner now may: "
                       + key_file().string()));
    EXPECT_EQ(identity.id().to_string(), python_id);
}

// A key file that holds no Ed25519 private key is refused, and kept as it
// is, rather than replaced with a new key.
TEST_F(NodeIdentityTest, AKeyFileWithNoEd25519PrivateKeyIsRefused) {
    EXPECT_TRUE(refused(""));
    EXPECT_TRUE(refused("not a key"));
    EXPECT_TRUE(refused(IMultiKey::Rsa(rsa_key_bits)->private_pem()));
    EXPECT_TRUE(refused(python_public_pem));
    EXPECT_EQ(read_file(key_file()), python_public_pem);
}

// A key, with lines after it to make the file 64 KiB, is read, and one byte
// more is not.
TEST_F(NodeIdentityTest, AKeyFileOfMoreThan64KiBIsRefused) {
    const string key(python_private_pem);

    EXPECT_FALSE(refused(key + string(most_key_file_size - key.size(), '\n')));
    EXPECT_TRUE(refused(key + string(most_key_file_size + 1 - key.size(), '\n')));
}

TEST_F(NodeIdentityTest, AKeyFileThatIsADirectoryIsRefused) {
    std::filesystem::create_directories(key_file());

    EXPECT_THROW(NodeIdentity(key_file(), store()), runtime_error);
}

TEST_F(NodeIdentityTest, AKeyFileInADirectoryNotThereIsNotRefused) {
    const auto missing = directory() / "missing" / "node-key.pem";

    (void)NodeIdentity(missing, store());
    EXPECT_TRUE(std::filesystem::exists(missing.parent_path()));
}
