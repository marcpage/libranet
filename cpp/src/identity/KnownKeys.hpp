#pragma once

#include <cstddef>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <string>

#include "crypto/ISignKey.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Constants.hpp"

namespace identity {

// The public keys of other nodes, by their ids (HTTP API §11.2). A node's id
// is the content identifier of its public key, PEM-encoded
// SubjectPublicKeyInfo, so its key is read from the store, which may hold it
// zlib-compressed. What is read is checked to hash to the id, and to be an
// Ed25519 public key that is not of small order. A key is kept once read, as
// the content an id names never changes, and is not read again. Safe to use
// from any thread.
class KnownKeys {
public:
    using Key = std::shared_ptr<const crypto::ISignKey>;

    // The most a key held compressed may decompress to, 64 KiB, as the
    // Python node allows: far more than the 113 bytes of an Ed25519 key's PEM.
    static constexpr std::size_t max_key_size = 64 * types::Kilobyte;

    // Throws std::invalid_argument for no store.
    explicit KnownKeys(storage::IDataSource::Ptr store);
    ~KnownKeys() = default;
    // Its mutex is neither copied nor moved.
    KnownKeys(const KnownKeys&) = delete;
    KnownKeys& operator=(const KnownKeys&) = delete;
    KnownKeys(KnownKeys&&) = delete;
    KnownKeys& operator=(KnownKeys&&) = delete;

    // The public key of the node whose id is node_id: the one kept, or else
    // the one read from the store. Nothing when the store does not hold it
    // yet, so that a signer whose key is not held yet can be told from one
    // whose signature fails (HandshakeProtocol §2). Throws
    // std::invalid_argument when what the store holds is not the content
    // node_id names, as it is or decompressed, or is not an Ed25519 public
    // key, or is one of small order.
    [[nodiscard]] std::optional<Key> key(const storage::Identifier& node_id);

private:
    storage::IDataSource::Ptr m_store;
    std::mutex m_mutex;
    std::map<std::string, Key> m_keys; // By node id; guarded by m_mutex

    [[nodiscard]] std::optional<Key> _kept(const storage::Identifier& node_id);
    [[nodiscard]] std::optional<Key> _read(const storage::Identifier& node_id);
    [[nodiscard]] Key _keep(const storage::Identifier& node_id, Key key);
};

} // namespace identity
