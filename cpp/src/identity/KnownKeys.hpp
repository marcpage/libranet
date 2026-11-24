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

// The public keys of other nodes, by their ids, read from the store and kept
// (HTTP API §11.2). Safe to use from any thread.
class KnownKeys {
public:
    using Key = std::shared_ptr<const crypto::ISignKey>;

    // 64 KiB, as the Python node allows: far more than an Ed25519 key's PEM.
    static constexpr std::size_t max_key_size = 64 * types::Kilobyte;

    // Throws std::invalid_argument for no store.
    explicit KnownKeys(storage::IDataSource::Ptr store);

    // Nothing when the store does not hold it yet, so that such a signer can
    // be told from one whose signature fails (HandshakeProtocol §2). Throws
    // std::invalid_argument when what the store holds is not node_id's
    // content, or not an Ed25519 public key that is not of small order.
    [[nodiscard]] std::optional<Key> key(const storage::Identifier& node_id);

    ~KnownKeys() = default;

    KnownKeys(const KnownKeys&) = delete;
    KnownKeys& operator=(const KnownKeys&) = delete;
    KnownKeys(KnownKeys&&) = delete;
    KnownKeys& operator=(KnownKeys&&) = delete;

private:
    storage::IDataSource::Ptr m_store;
    std::mutex m_mutex;
    std::map<std::string, Key> m_keys; // By node id; guarded by m_mutex

    [[nodiscard]] std::optional<Key> _kept(const storage::Identifier& node_id);
    [[nodiscard]] std::optional<Key> _read(const storage::Identifier& node_id);
    [[nodiscard]] Key _keep(const storage::Identifier& node_id, Key key);
};

} // namespace identity
