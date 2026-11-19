#pragma once

#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "crypto/IHasher.hpp"

namespace storage {

class Identifier {
public:
    using Data = std::vector<std::byte>;

    static Identifier from(const std::string& identifier_str);
    static Identifier from(const Data& data);

    Identifier() = default;
    // Throws std::invalid_argument for an algorithm that is not supported.
    Identifier(std::string algorithm, std::string hash);
    ~Identifier() = default;
    Identifier(const Identifier&) = default;
    Identifier& operator=(const Identifier&) = default;
    Identifier(Identifier&&) = default;
    Identifier& operator=(Identifier&&) = default;

    [[nodiscard]] const std::string& algorithm() const;
    [[nodiscard]] const std::string& hash() const;
    [[nodiscard]] std::string to_string() const;

private:
    // The only hash algorithm supported (HttpApi §5.4: an unknown one is
    // refused). The algorithm names a directory in a store, so a name not
    // on this list, such as "..", must never reach a path.
    static constexpr std::string_view sha256 = "sha256";

    std::string m_algorithm;
    std::string m_hash;
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
    if (sha256 != m_algorithm) {
        throw std::invalid_argument("Invalid identifier format: unsupported algorithm");
    }
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

} // namespace storage
