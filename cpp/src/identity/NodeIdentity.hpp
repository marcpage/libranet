#pragma once

#include <cstddef>
#include <filesystem>
#include <string>
#include <string_view>
#include <vector>

#include "crypto/ISignKey.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"

namespace identity {

// The node's own Ed25519 key, and the id other nodes know it by (HTTP API
// §11.2). Any thread may use it.
class NodeIdentity {
public:
    using Data = std::vector<std::byte>;

    // The key kept in key_file, or a new one written there, readable only by
    // its owner; its public key is put in store. Throws std::invalid_argument
    // when key_file holds no Ed25519 private key, and std::runtime_error when
    // it cannot be read or written, or store cannot keep the public key.
    NodeIdentity(const std::filesystem::path& key_file, const storage::IDataSource& store);

    // The identifier of public_pem(), a signature's keyid (HTTP API §11.2).
    [[nodiscard]] const storage::Identifier& id() const noexcept;
    // As PEM-encoded SubjectPublicKeyInfo.
    [[nodiscard]] const std::string& public_pem() const noexcept;
    [[nodiscard]] Data sign(std::string_view data) const;

    ~NodeIdentity() = default;
    NodeIdentity(NodeIdentity&&) noexcept = default;
    NodeIdentity& operator=(NodeIdentity&&) noexcept = default;

    NodeIdentity(const NodeIdentity&) = delete;
    NodeIdentity& operator=(const NodeIdentity&) = delete;

private:
    crypto::ISignKey::Ptr m_key;
    std::string m_public_pem;
    storage::Identifier m_id;
};

} // namespace identity
