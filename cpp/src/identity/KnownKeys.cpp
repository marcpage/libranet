#include "KnownKeys.hpp"

#include <mutex>
#include <optional>
#include <stdexcept>
#include <utility>

#include "crypto/Ed25519OpenSsl.hpp"
#include "storage/IDataSource.hpp"
#include "storage/Identifier.hpp"
#include "types/Converter.hpp"

using identity::KnownKeys;
using std::invalid_argument;
using std::nullopt;
using std::optional;
using std::scoped_lock;
using storage::IDataSource;
using storage::Identifier;

namespace {

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
KnownKeys::Key public_key_of(const IDataSource::Data& content) {
    KnownKeys::Key key = crypto::Ed25519OpenSsl::from_pem(types::Converter::text_of(content));

    if (key->has_private_key()) {
        throw invalid_argument("A private key, not a public key");
    }

    return key;
}

// The Ed25519 public key the node whose id is node_id publishes, read from
// body as the store holds it. Throws std::invalid_argument for a body that is
// not one, saying of which node.
KnownKeys::Key published_key(const Identifier& node_id, const IDataSource::Data& body) {
    try {
        return public_key_of(content_of(node_id, body));
    } catch (const invalid_argument& error) {
        throw invalid_argument("No usable public key for " + node_id.to_string() + ": "
                               + error.what());
    }
}

} // namespace

namespace identity {

KnownKeys::KnownKeys(IDataSource::Ptr store) : m_store(std::move(store)) {
    if (!m_store) {
        throw invalid_argument("Known keys are read from a store, and none was given");
    }
}

optional<KnownKeys::Key> KnownKeys::key(const Identifier& node_id) {
    auto found = _kept(node_id);

    if (!found) {
        found = _read(node_id);
    }

    return found;
}

// The key kept for node_id, if there is one.
optional<KnownKeys::Key> KnownKeys::_kept(const Identifier& node_id) {
    const scoped_lock lock(m_mutex);
    const auto found = m_keys.find(node_id.to_string());

    if (found == m_keys.end()) {
        return nullopt;
    }

    return found->second;
}

// The key of node_id read from the store, and kept; nothing when the store
// does not hold it. The store is read without the lock held, so that a slow
// read does not hold up others.
optional<KnownKeys::Key> KnownKeys::_read(const Identifier& node_id) {
    const auto body = m_store->get(node_id);

    if (!body) {
        return nullopt;
    }

    return _keep(node_id, published_key(node_id, *body));
}

// The key kept for node_id: key, unless another thread that read it at the
// same time kept its own first, so that every caller has the same one.
KnownKeys::Key KnownKeys::_keep(const Identifier& node_id, Key key) {
    const scoped_lock lock(m_mutex);

    return m_keys.emplace(node_id.to_string(), std::move(key)).first->second;
}

} // namespace identity
