#pragma once

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cstddef>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "crypto/IHasher.hpp"
#include "logger/Logger.hpp"

namespace storage {

class Identifier {
public:
    using Data = std::vector<std::byte>;

    static Identifier from(const std::string& identifier_str);
    static Identifier from(const Data& data);

    Identifier() = default;
    // Throws std::invalid_argument for an algorithm that is not supported,
    // or a hash that is not as many hex digits as the algorithm's digest
    // has. The hash may be in any case, and is kept in lower case
    // (HttpApi §5.4).
    Identifier(std::string algorithm, std::string hash);
    ~Identifier() = default;
    Identifier(const Identifier&) = default;
    Identifier& operator=(const Identifier&) = default;
    Identifier(Identifier&&) = default;
    Identifier& operator=(Identifier&&) = default;

    [[nodiscard]] const std::string& algorithm() const;
    [[nodiscard]] const std::string& hash() const;
    [[nodiscard]] std::string to_string() const;
    // How many hex digits the longest hash of a supported algorithm has.
    [[nodiscard]] static std::size_t longest_hash_digits();

private:
    // The only hash algorithm supported (HttpApi §5.4: an unknown one is
    // refused). The algorithm names a directory in a store, so a name not
    // on this list, such as "..", must never reach a path.
    static constexpr std::string_view sha256 = "sha256";

    std::string m_algorithm;
    std::string m_hash;

    [[nodiscard]] static std::size_t _sha256_digits();
    static void _warn_unsupported(const std::string& algorithm);
};

inline Identifier Identifier::from(const std::string& identifier_str) {
    std::string_view identifier_view(identifier_str);
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

inline Identifier Identifier::from(const Data& data) {
    return Identifier(std::string(sha256), crypto::IHasher::Sha256()->hex_digest(data));
}

inline Identifier::Identifier(std::string algorithm, std::string hash)
    : m_algorithm(std::move(algorithm)), m_hash(std::move(hash)) {
    constexpr std::string_view hex_digits = "0123456789abcdefABCDEF";

    if (sha256 != m_algorithm) {
        _warn_unsupported(m_algorithm);
        throw std::invalid_argument("Invalid identifier format: unsupported algorithm");
    }

    if (m_hash.size() != _sha256_digits()
        || m_hash.find_first_not_of(hex_digits) != std::string::npos) {
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

// SHA-256 is the only algorithm supported, so its hash is the longest.
inline std::size_t Identifier::longest_hash_digits() {
    return _sha256_digits();
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
