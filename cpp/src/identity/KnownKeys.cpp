#include "KnownKeys.hpp"

#include <map>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>

#include "crypto/Ed25519OpenSsl.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Converter.hpp"

using identity::KnownKeys;
using std::invalid_argument;
using std::map;
using std::mutex;
using std::nullopt;
using std::optional;
using std::scoped_lock;
using std::string;
using storage::IDataSource;
using storage::Identifier;

namespace {

using Key = KnownKeys::Key;
using Keys = map<string, Key>;

// The content node_id names, read from body as the store holds it: body
// itself, or what it decompresses to. Throws std::invalid_argument when it is
// neither.
IDataSource::Data content_of(const Identifier& node_id, const IDataSource::Data& body) {
    auto content = node_id.content_of(body, KnownKeys::max_key_size);

    if (!content) {
        throw invalid_argument("What is held as the key of " + node_id.to_string()
                               + " does not hash to it, as it is or decompressed");
    }

    return std::move(*content);
}

// The Ed25519 public key that content, PEM-encoded SubjectPublicKeyInfo, is.
// Throws std::invalid_argument for content that is not one, as a private key
// is not, or that is one of small order, which anyone can sign for.
Key public_key_of(const IDataSource::Data& content) {
    Key key = crypto::Ed25519OpenSsl::from_pem(types::Converter::text_of(content));

    if (key->has_private_key()) {
        throw invalid_argument("A private key, not a public key");
    }

    return key;
}

// The Ed25519 public key the node whose id is node_id publishes, read from
// body as the store holds it. Throws std::invalid_argument for a body that is
// not one, saying of which node.
Key published_key(const Identifier& node_id, const IDataSource::Data& body) {
    try {
        return public_key_of(content_of(node_id, body));
    } catch (const invalid_argument& error) {
        throw invalid_argument("No usable public key for " + node_id.to_string() + ": "
                               + error.what());
    }
}

// The key keys keeps for node_id, if there is one. guard guards keys.
optional<Key> kept_key(mutex& guard, const Keys& keys, const Identifier& node_id) {
    const scoped_lock lock(guard);
    const auto found = keys.find(node_id.to_string());

    if (found == keys.end()) {
        return nullopt;
    }

    return found->second;
}

// The key keys keeps for node_id: key, unless another thread that read it at
// the same time kept its own first, so that every caller has the same one.
// guard guards keys.
Key keep_key(mutex& guard, Keys& keys, const Identifier& node_id, Key key) {
    const scoped_lock lock(guard);

    return keys.emplace(node_id.to_string(), std::move(key)).first->second;
}

// The key of node_id read from store, and kept in keys, which guard guards;
// nothing when store does not hold it. store is read without the lock held,
// so that a slow read does not hold up others.
optional<Key> read_key(const IDataSource& store, mutex& guard, Keys& keys,
                       const Identifier& node_id) {
    const auto body = store.get(node_id);

    if (!body) {
        return nullopt;
    }

    return keep_key(guard, keys, node_id, published_key(node_id, *body));
}

} // namespace

namespace identity {

KnownKeys::KnownKeys(IDataSource::Ptr store) : m_store(std::move(store)) {
    if (!m_store) {
        throw invalid_argument("Known keys are read from a store, and none was given");
    }
}

optional<KnownKeys::Key> KnownKeys::key(const Identifier& node_id) {
    auto found = kept_key(m_mutex, m_keys, node_id);

    if (!found) {
        found = read_key(*m_store, m_mutex, m_keys, node_id);
    }

    return found;
}

} // namespace identity
