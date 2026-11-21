#pragma once

#include <algorithm>
#include <array>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <variant>

#include "crypto/IHasher.hpp"
#include "http/StructuredField.hpp"

namespace identity {

// A Content-Digest field (RFC 9530 §2): a dictionary of digests of a body,
// each a byte sequence under the key of its algorithm. A signature covers
// headers, not the body, so one that covers content-digest covers the body
// only once the digest is checked against the bytes received (HTTP API §11).
// A node sends the sha-256 of a body. Of a field it receives, it passes over
// the algorithms it does not know, but needs at least one it knows, and
// checks every one it knows against the body (HTTP API §11.2).
class ContentDigest {
public:
    // The field a node sends for body: its sha-256.
    [[nodiscard]] static ContentDigest of(std::string_view body);
    // Reads a Content-Digest field's value. Throws std::invalid_argument for
    // text that is not a dictionary, that names no algorithm known here, or
    // that gives a digest of one as other than a byte sequence.
    [[nodiscard]] static ContentDigest parse(std::string_view text);

    // Whether each digest of an algorithm known here is that of body.
    [[nodiscard]] bool matches(std::string_view body) const;
    // The field's value, as RFC 9651 §4.1.2 writes a dictionary.
    [[nodiscard]] std::string to_string() const;

private:
    using Bytes = http::StructuredField::Bytes;
    // Makes what hashes with an algorithm.
    using MakeHasher = crypto::IHasher::Ptr (*)();

    // A hash algorithm, by its key in RFC 9530's Hash Algorithms for HTTP
    // Digest Fields registry.
    struct Algorithm {
        std::string_view key;
        MakeHasher make_hasher = nullptr;
    };

    // The algorithms known here, the one a node sends first (HTTP API
    // §11.2).
    static constexpr std::array<Algorithm, 1> known_algorithms{
        Algorithm{"sha-256", &crypto::IHasher::Sha256},
    };

    http::StructuredField m_digests;

    explicit ContentDigest(http::StructuredField digests);

    [[nodiscard]] std::optional<Bytes> _digest(const Algorithm& algorithm) const;
    [[nodiscard]] static Bytes _hash(const Algorithm& algorithm, std::string_view body);
};

inline ContentDigest ContentDigest::of(std::string_view body) {
    const auto& algorithm = known_algorithms.front();
    http::StructuredField::Item digest{_hash(algorithm, body), {}};
    http::StructuredField digests;

    digests.set(std::string(algorithm.key), std::move(digest));
    return ContentDigest(std::move(digests));
}

inline ContentDigest ContentDigest::parse(std::string_view text) {
    return ContentDigest(http::StructuredField::parse(text));
}

inline bool ContentDigest::matches(std::string_view body) const {
    const auto is_of_body = [this, body](const Algorithm& algorithm) {
        const auto digest = _digest(algorithm);

        return !digest || _hash(algorithm, body) == *digest;
    };

    return std::all_of(known_algorithms.begin(), known_algorithms.end(), is_of_body);
}

inline std::string ContentDigest::to_string() const {
    return m_digests.to_string();
}

// Throws std::invalid_argument for digests that name no algorithm known
// here, or that give a digest of one as other than a byte sequence.
inline ContentDigest::ContentDigest(http::StructuredField digests)
    : m_digests(std::move(digests)) {
    const auto is_given = [this](const Algorithm& algorithm) {
        return _digest(algorithm).has_value();
    };

    if (std::none_of(known_algorithms.begin(), known_algorithms.end(), is_given)) {
        throw std::invalid_argument("A Content-Digest names no algorithm known here: "
                                    + m_digests.to_string());
    }
}

// The digest given under algorithm's key, if there is one. Throws
// std::invalid_argument for a key that names other than a byte sequence.
inline std::optional<ContentDigest::Bytes>
ContentDigest::_digest(const Algorithm& algorithm) const {
    const auto member = m_digests.member(algorithm.key);

    if (!member) {
        return std::nullopt;
    }

    const auto* const item = std::get_if<http::StructuredField::Item>(&*member);
    const auto* const digest = nullptr == item ? nullptr : std::get_if<Bytes>(&item->value);

    if (nullptr == digest) {
        throw std::invalid_argument("A Content-Digest gives a " + std::string(algorithm.key)
                                    + " digest as a byte sequence");
    }

    return *digest;
}

inline ContentDigest::Bytes ContentDigest::_hash(const Algorithm& algorithm,
                                                 std::string_view body) {
    const auto hasher = algorithm.make_hasher();

    hasher->update(body);
    return hasher->digest();
}

} // namespace identity
