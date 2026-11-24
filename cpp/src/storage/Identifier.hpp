#pragma once

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cstddef>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "crypto/IHasher.hpp"
#include "logger/Logger.hpp"
#include "storage/Zlib.hpp"
#include "types/Constants.hpp"
#include "types/Json.hpp"

namespace storage {

class Identifier {
public:
    using Data = std::vector<std::byte>;

    [[nodiscard]] static Identifier parse(std::string_view text);
    [[nodiscard]] static Identifier of(const Data& data);
    // Nothing for a value that is not a string of "{algorithm}/{hash}" of a
    // supported algorithm, which is warned of as the constructor warns of it.
    [[nodiscard]] static std::optional<Identifier> from_json(const types::Json& value);

    // Throws std::invalid_argument for an algorithm that is not supported, or
    // a hash that is not its digest's hex digits. The hash is kept in lower
    // case (HttpApi §5.4).
    Identifier(std::string algorithm, std::string hash);

    [[nodiscard]] const std::string& algorithm() const;
    [[nodiscard]] const std::string& hash() const;
    [[nodiscard]] std::string to_string() const;
    // body itself, when it hashes to this identifier, or what it decompresses
    // to, when that does (HighLevelDesign §4.1.1, HttpApi §8). Nothing when it
    // is neither, or decompresses to more than limit bytes. Throws
    // std::bad_alloc if zlib runs out of memory.
    [[nodiscard]] std::optional<Data> content_of(const Data& body, std::size_t limit) const;
    [[nodiscard]] static std::size_t longest_hash_digits();
    // So that an identifier under an unknown algorithm can be told from one
    // that is not valid.
    [[nodiscard]] static bool is_supported(std::string_view algorithm) noexcept;

    Identifier() = default;
    ~Identifier() = default;
    Identifier(const Identifier&) = default;
    Identifier& operator=(const Identifier&) = default;
    Identifier(Identifier&&) = default;
    Identifier& operator=(Identifier&&) = default;

private:
    // HttpApi §5.4 refuses any other. The algorithm names a directory in a
    // store, so a name not on this list, such as "..", must never reach a
    // path.
    static constexpr std::string_view sha256 = "sha256";

    std::string m_algorithm;
    std::string m_hash;

    [[nodiscard]] bool _is_hash_of(const Data& data) const;
    [[nodiscard]] static std::size_t _sha256_digits();
    static void _warn_unsupported(const std::string& algorithm);
};

inline Identifier Identifier::parse(std::string_view text) {
    auto identifier_view = text;
    const auto start = identifier_view.find_first_not_of('/');

    if (start == std::string_view::npos) {
        throw std::invalid_argument("Invalid identifier format: empty or contains only slashes.");
    }

    identifier_view.remove_prefix(start);

    const auto first_slash = identifier_view.find('/');

    if (first_slash == std::string_view::npos) {
        throw std::invalid_argument("Invalid identifier format: missing hash component.");
    }

    const std::string algorithm(identifier_view.substr(0, first_slash));

    identifier_view.remove_prefix(first_slash + 1);

    const auto second_slash = identifier_view.find('/');
    const std::string hash(identifier_view.substr(0, second_slash));

    return Identifier(algorithm, hash);
}

inline Identifier Identifier::of(const Data& data) {
    return Identifier(std::string(sha256), crypto::IHasher::Sha256()->hex_digest(data));
}

inline std::optional<Identifier> Identifier::from_json(const types::Json& value) {
    if (!value.is_string()) {
        return std::nullopt;
    }

    const auto& text = value.string();
    const auto slash = text.find('/');

    // Text with no algorithm before its slash is refused here, so that it
    // is not warned of as an algorithm the node does not know.
    if (0 == slash || std::string::npos == slash) {
        return std::nullopt;
    }

    try {
        return Identifier(text.substr(0, slash), text.substr(slash + 1));
    } catch (const std::invalid_argument&) {
        // Not an identifier of a supported algorithm: nothing.
        return std::nullopt;
    }
}

inline Identifier::Identifier(std::string algorithm, std::string hash)
    : m_algorithm(std::move(algorithm)), m_hash(std::move(hash)) {
    if (!is_supported(m_algorithm)) {
        _warn_unsupported(m_algorithm);
        throw std::invalid_argument("Invalid identifier format: unsupported algorithm");
    }

    if (m_hash.size() != _sha256_digits()
        || m_hash.find_first_not_of(types::hex_digits_either_case) != std::string::npos) {
        throw std::invalid_argument("Invalid identifier format: hash is not hex digits, as many "
                                    "as the algorithm's digest has");
    }

    std::transform(m_hash.begin(), m_hash.end(), m_hash.begin(), [](char digit) {
        return static_cast<char>(std::tolower(static_cast<unsigned char>(digit)));
    });
}

inline const std::string& Identifier::algorithm() const {
    return m_algorithm;
}

inline const std::string& Identifier::hash() const {
    return m_hash;
}

inline std::string Identifier::to_string() const {
    return m_algorithm + "/" + m_hash;
}

inline std::optional<Identifier::Data> Identifier::content_of(const Data& body,
                                                              std::size_t limit) const {
    if (_is_hash_of(body)) {
        return body;
    }

    auto content = Zlib::decompress(body, limit);

    if (!content || !_is_hash_of(*content)) {
        return std::nullopt;
    }

    return content;
}

// SHA-256 is the only algorithm supported, so its hash is the longest.
inline std::size_t Identifier::longest_hash_digits() {
    return _sha256_digits();
}

inline bool Identifier::is_supported(std::string_view algorithm) noexcept {
    return sha256 == algorithm;
}

// Whether data hashes to this identifier. SHA-256 is the only algorithm
// supported, so an identifier made with no algorithm names no data.
inline bool Identifier::_is_hash_of(const Data& data) const {
    const auto hashed = of(data);

    return hashed.algorithm() == m_algorithm && hashed.hash() == m_hash;
}

// How many hex digits a SHA-256 hash has: two for each byte of its digest.
// Found when first asked, so that a hasher is made only once.
inline std::size_t Identifier::_sha256_digits() {
    constexpr std::size_t digits_per_byte = 2;
    static const std::size_t digits = crypto::IHasher::Sha256()->digest_size() * digits_per_byte;

    return digits;
}

// Warns of the first algorithm refused, from any thread, since content
// under one the node does not know may mean the node needs updating. Those
// after it are not logged, so that requests for them cannot fill the log.
inline void Identifier::_warn_unsupported(const std::string& algorithm) {
    static std::atomic<bool> warned{false};

    if (!warned.exchange(true)) {
        lWarn << "The node may need updating: it does not know the hash algorithm" << algorithm
              << "(only the first unknown algorithm is logged)";
    }
}

} // namespace storage
