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

// A Content-Digest field (RFC 9530 §2). A node sends the sha-256 of a body,
// and of a field it receives, checks every algorithm it knows and passes over
// the rest (HTTP API §11.2).
class ContentDigest {
public:
    // The name of the field (RFC 9530 §2).
    static constexpr std::string_view field_name = "Content-Digest";

    // Its sha-256.
    [[nodiscard]] static ContentDigest of(std::string_view body);
    // Throws std::invalid_argument for text that is not a dictionary, names no
    // algorithm known here, or gives a digest that is not a byte sequence.
    [[nodiscard]] static ContentDigest parse(std::string_view text);

    // Whether each digest of an algorithm known here is that of body.
    [[nodiscard]] bool matches(std::string_view body) const;
    // As RFC 9651 §4.1.2 writes a dictionary.
    [[nodiscard]] std::string to_string() const;

private:
    using Bytes = http::StructuredField::Bytes;
    using MakeHasher = crypto::IHasher::Ptr (*)();

    // By its key in RFC 9530's Hash Algorithms for HTTP Digest Fields
    // registry.
    struct Algorithm {
        std::string_view key;
        MakeHasher make_hasher = nullptr;
    };

    // The one a node sends first (HTTP API §11.2).
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
    if (std::none_of(known_algorithms.begin(), known_algorithms.end(),
                     [this](const Algorithm& algorithm) {
        return _digest(algorithm).has_value();
    })) {
        throw std::invalid_argument("A Content-Digest names no algorithm known here: "
                                    + m_digests.to_string());
    }
}

// The digest given under algorithm's key, if there is one. Throws
// std::invalid_argument for a key that names other than a byte sequence.
inline std::optional<ContentDigest::Bytes> ContentDigest::_digest(
    const Algorithm& algorithm) const {
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
