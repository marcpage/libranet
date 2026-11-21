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

// The node's own Ed25519 key, and the id other nodes know it by: the
// identifier of its public key, as PEM-encoded SubjectPublicKeyInfo, which
// is stored as content so that they can fetch it (HTTP API §11.2).
// ISignKey::id() is the same for every Ed25519 key, so the node's id comes
// from here. Nothing changes once it is made, so any thread may use it.
class NodeIdentity {
public:
    using Data = std::vector<std::byte>;

    // The key kept in key_file, or, when there is no file there, a new one,
    // written to a file only its owner may read. A key file others may read
    // is warned of, and made readable only by its owner. The public key is
    // put in store, unless store holds it already. Throws
    // std::invalid_argument when key_file holds no Ed25519 private key, and
    // std::runtime_error when key_file cannot be read or written, as when
    // its directory is not there, or store cannot keep the public key.
    NodeIdentity(const std::filesystem::path& key_file, const storage::IDataSource& store);
    ~NodeIdentity() = default;
    NodeIdentity(const NodeIdentity&) = delete;
    NodeIdentity& operator=(const NodeIdentity&) = delete;
    NodeIdentity(NodeIdentity&&) noexcept = default;
    NodeIdentity& operator=(NodeIdentity&&) noexcept = default;

    // The identifier of the public key, which a signature names as its
    // keyid (HTTP API §11.2).
    [[nodiscard]] const storage::Identifier& id() const noexcept;
    // The public key, as PEM-encoded SubjectPublicKeyInfo: the content id()
    // names.
    [[nodiscard]] const std::string& public_pem() const noexcept;
    // The signature of data, with the node's key.
    [[nodiscard]] Data sign(std::string_view data) const;

private:
    crypto::ISignKey::Ptr m_key;
    std::string m_public_pem;
    storage::Identifier m_id;
};

} // namespace identity
