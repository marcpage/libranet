#include <gtest/gtest.h>
#include <cstddef>
#include <cstdint>
#include <map>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include "crypto/ISignKey.hpp"
#include "crypto/Pem.hpp"
#include "identity/KnownKeys.hpp"
#include "storage/HashPrefix.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "storage/Zlib.hpp"
#include "types/Converter.hpp"

using crypto::ISignKey;
using identity::KnownKeys;
using storage::IDataSource;
using storage::Identifier;
using types::Converter;

namespace {

// RFC 8032's first Ed25519 test vector: its public key in the PEM form
// Python's cryptography package writes, as the Python node publishes its
// key, and its signature of the empty message.
constexpr auto rfc_8032_public_pem =
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEA11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo=\n"
    "-----END PUBLIC KEY-----\n";
constexpr auto rfc_8032_signature =
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555"
    "fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b";

// An X25519 public key: the same size as an Ed25519 one, for key agreement.
constexpr auto x25519_public_pem =
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VuAyEAj0DFrbaPJWJK5bIU6nZ6bslNgp09e14a0bpvPiE4KF8=\n"
    "-----END PUBLIC KEY-----\n";

// The SubjectPublicKeyInfo of the Ed25519 public key that is the point of
// order 1, the neutral element, whose raw key is 1 and zeros (RFC 8032
// §5.1.2), in hex.
constexpr auto small_order_der =
    "302a300506032b6570032100"
    "0100000000000000000000000000000000000000000000000000000000000000";

// A store that keeps what it is given in memory, by identifier. put() is
// const in IDataSource, so what it keeps is mutable. Only the tests' own
// thread puts, so threads that only get need no lock.
class MemorySource : public IDataSource {
public:
    [[nodiscard]] bool has(const Identifier& identifier) const override {
        return m_data.find(identifier.to_string()) != m_data.end();
    }

    [[nodiscard]] std::optional<Data> get(const Identifier& identifier) const override {
        const auto found = m_data.find(identifier.to_string());

        if (found == m_data.end()) {
            return std::nullopt;
        }

        return found->second;
    }

    void put(const Identifier& identifier, const Data& body) const override {
        m_data[identifier.to_string()] = body;
    }

    [[nodiscard]] bool remove(const Identifier& identifier) const override {
        return m_data.erase(identifier.to_string()) > 0;
    }

    [[nodiscard]] std::uintmax_t size() const override {
        return m_data.size();
    }

    [[nodiscard]] Identifiers starting_with(
        [[maybe_unused]] const storage::HashPrefix& prefix) const override {
        return {};
    }

private:
    mutable std::map<std::string, Data> m_data;
};

// The bytes hex names, two digits a byte.
IDataSource::Data bytes_of_hex(std::string_view hex) {
    constexpr auto hex_base = 16;
    IDataSource::Data bytes;

    for (std::size_t index = 0; index < hex.size(); index += 2) {
        const auto digits = std::string(hex.substr(index, 2));

        bytes.push_back(static_cast<std::byte>(std::stoul(digits, nullptr, hex_base)));
    }

    return bytes;
}

// Stores text in store as content, under its identifier, which comes back.
Identifier store_content(const IDataSource& store, std::string_view text) {
    const auto content = Converter::bytes_of(text);
    const auto node_id = Identifier::of(content);

    store.put(node_id, content);
    return node_id;
}

// What KnownKeys::key() says is wrong with what the store holds for node_id;
// empty if it reads a key.
std::string refusal(KnownKeys& keys, const Identifier& node_id) {
    try {
        (void)keys.key(node_id);
    } catch (const std::invalid_argument& error) {
        return error.what();
    }

    return {};
}

// Whether message names what it says, as part of it.
bool says(const std::string& message, std::string_view part) {
    return message.find(part) != std::string::npos;
}

} // namespace

// The tests read optionals after ASSERT_TRUE(x.has_value()), which this
// check does not follow.
// NOLINTBEGIN(bugprone-unchecked-optional-access)

// The Ed25519 public key a node publishes, as the Python node publishes it,
// is read, and checks the signatures its private key made. Its id, the
// SHA-256 of the PEM, is as `shasum -a 256` gives it.
TEST(KnownKeysTest, KeyPublishedAsPythonDoesIsRead) {
    const auto store = std::make_shared<MemorySource>();
    const auto node_id = store_content(*store, rfc_8032_public_pem);
    KnownKeys keys(store);
    const auto key = keys.key(node_id);

    EXPECT_EQ(node_id.to_string(),
              "sha256/7f2d9ed0b71b8e5a6c5cf30e647d6e20b5bca6dac8071f11abe3fef8014db610");
    ASSERT_TRUE(key.has_value());
    EXPECT_FALSE((*key)->has_private_key());
    EXPECT_EQ((*key)->public_pem(), rfc_8032_public_pem);
    EXPECT_TRUE((*key)->verify("", bytes_of_hex(rfc_8032_signature)));
    EXPECT_FALSE((*key)->verify("x", bytes_of_hex(rfc_8032_signature)));
}

TEST(KnownKeysTest, KeyOfANewNodeChecksItsSignatures) {
    const auto signer = ISignKey::Ed25519();
    const auto store = std::make_shared<MemorySource>();
    const auto node_id = store_content(*store, signer->public_pem());
    KnownKeys keys(store);
    const auto key = keys.key(node_id);

    ASSERT_TRUE(key.has_value());
    EXPECT_TRUE((*key)->verify("message", signer->sign("message")));
    EXPECT_FALSE((*key)->verify("message", ISignKey::Ed25519()->sign("message")));
}

// A key may be held compressed, as any content may (HTTP API §8, §11.2).
TEST(KnownKeysTest, CompressedKeyIsRead) {
    const auto store = std::make_shared<MemorySource>();
    const auto content = Converter::bytes_of(rfc_8032_public_pem);
    const auto node_id = Identifier::of(content);
    KnownKeys keys(store);

    store->put(node_id, storage::Zlib::compress(content, storage::Zlib::max_level));

    const auto key = keys.key(node_id);

    ASSERT_TRUE(key.has_value());
    EXPECT_EQ((*key)->public_pem(), rfc_8032_public_pem);
}

TEST(KnownKeysTest, KeyNotHeldIsNothing) {
    const auto store = std::make_shared<MemorySource>();
    KnownKeys keys(store);
    const auto node_id = Identifier::of(Converter::bytes_of(rfc_8032_public_pem));

    EXPECT_FALSE(keys.key(node_id).has_value());

    // Once the store holds it, it is read.
    (void)store_content(*store, rfc_8032_public_pem);
    EXPECT_TRUE(keys.key(node_id).has_value());
}

// A key once read is kept, and not read from the store again: every caller
// has the same one, though the store no longer holds it.
TEST(KnownKeysTest, KeyIsKeptOnceRead) {
    const auto store = std::make_shared<MemorySource>();
    const auto node_id = store_content(*store, rfc_8032_public_pem);
    KnownKeys keys(store);
    const auto first = keys.key(node_id);

    EXPECT_TRUE(store->remove(node_id));

    const auto second = keys.key(node_id);

    ASSERT_TRUE(first.has_value());
    ASSERT_TRUE(second.has_value());
    EXPECT_EQ(*first, *second);
}

TEST(KnownKeysTest, BodyThatDoesNotHashToTheIdIsRefused) {
    const auto store = std::make_shared<MemorySource>();
    const auto node_id = Identifier::of(Converter::bytes_of(rfc_8032_public_pem));
    KnownKeys keys(store);

    store->put(node_id, Converter::bytes_of(ISignKey::Ed25519()->public_pem()));

    const auto message = refusal(keys, node_id);

    EXPECT_TRUE(says(message, node_id.to_string()));
    EXPECT_TRUE(says(message, "does not hash to it"));
}

// What is refused is not kept: once the store holds the key, it is read.
TEST(KnownKeysTest, RefusalIsNotKept) {
    const auto store = std::make_shared<MemorySource>();
    const auto content = Converter::bytes_of(rfc_8032_public_pem);
    const auto node_id = Identifier::of(content);
    KnownKeys keys(store);

    store->put(node_id, Converter::bytes_of("not the key"));
    EXPECT_FALSE(refusal(keys, node_id).empty());

    store->put(node_id, content);
    EXPECT_TRUE(keys.key(node_id).has_value());
}

TEST(KnownKeysTest, ContentThatIsNoKeyIsRefused) {
    const auto store = std::make_shared<MemorySource>();
    KnownKeys keys(store);

    EXPECT_FALSE(refusal(keys, store_content(*store, "hello")).empty());
    EXPECT_FALSE(refusal(keys, store_content(*store, "-----BEGIN PUBLIC KEY-----\nYWJj\n"
                                                     "-----END PUBLIC KEY-----\n"))
                     .empty());
}

TEST(KnownKeysTest, KeyOfAnotherAlgorithmIsRefused) {
    const auto store = std::make_shared<MemorySource>();
    KnownKeys keys(store);

    EXPECT_FALSE(refusal(keys, store_content(*store, x25519_public_pem)).empty());
}

TEST(KnownKeysTest, PrivateKeyIsRefused) {
    const auto store = std::make_shared<MemorySource>();
    KnownKeys keys(store);
    const auto node_id = store_content(*store, ISignKey::Ed25519()->private_pem());

    EXPECT_TRUE(says(refusal(keys, node_id), "private key"));
}

// Anyone can sign for a key of small order (HTTP API §11.2).
TEST(KnownKeysTest, KeyOfSmallOrderIsRefused) {
    const auto store = std::make_shared<MemorySource>();
    KnownKeys keys(store);
    const auto pem = crypto::Pem::wrap("PUBLIC KEY", bytes_of_hex(small_order_der));

    EXPECT_TRUE(says(refusal(keys, store_content(*store, pem)), "small order"));
}

TEST(KnownKeysTest, NoStoreIsRefused) {
    EXPECT_THROW((void)KnownKeys(nullptr), std::invalid_argument);
}

// Several threads asking for one key at once, which ThreadSanitizer checks,
// all have the same one.
TEST(KnownKeysTest, ThreadsAskingAtOnceShareOneKey) {
    constexpr std::size_t thread_count = 8;
    const auto store = std::make_shared<MemorySource>();
    const auto node_id = store_content(*store, rfc_8032_public_pem);
    KnownKeys keys(store);
    std::vector<KnownKeys::Key> found(thread_count);
    std::vector<std::thread> threads;

    threads.reserve(thread_count);

    for (std::size_t index = 0; index < thread_count; ++index) {
        threads.emplace_back([&, index] { found.at(index) = keys.key(node_id).value_or(nullptr); });
    }

    for (auto& thread : threads) {
        thread.join();
    }

    for (const auto& key : found) {
        EXPECT_NE(key, nullptr);
        EXPECT_EQ(key, found.front());
    }
}

// NOLINTEND(bugprone-unchecked-optional-access)
