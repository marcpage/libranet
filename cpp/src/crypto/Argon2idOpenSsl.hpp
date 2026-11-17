#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <openssl/core_names.h>
#include <openssl/crypto.h>
#include <openssl/kdf.h>
#include <openssl/params.h>
#include <openssl/rand.h>

#include "Base64.hpp"
#include "IPasswordHasher.hpp"
#include "OpenSsl.hpp"

namespace crypto {

// Argon2id password hashing, built on OpenSSL. A hash is kept in the PHC
// string format that libargon2 and Python's cryptography package write,
// such as $argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>, with the salt and
// the hash in Base64 without padding. A hash is checked with the costs it
// was made with, which it names.
class Argon2idOpenSsl : public IPasswordHasher {
public:
    Argon2idOpenSsl(
        uint32_t memory_cost_kib,
        uint32_t iterations,
        uint32_t parallelism,
        std::size_t salt_length,
        std::size_t hash_length);

    ~Argon2idOpenSsl() override = default;

    Argon2idOpenSsl(const Argon2idOpenSsl&) = delete;
    Argon2idOpenSsl& operator=(const Argon2idOpenSsl&) = delete;
    Argon2idOpenSsl(Argon2idOpenSsl&&) noexcept = default;
    Argon2idOpenSsl& operator=(Argon2idOpenSsl&&) noexcept = default;

    [[nodiscard]] std::string hash_password(std::string_view password) const override;
    [[nodiscard]] bool verify_password(std::string_view password,
                                       std::string_view hash) const override;

private:
    using Data = std::vector<std::byte>;

    // What a hash is made with, besides the password.
    struct Parameters {
        uint32_t memory_cost_kib = 0;
        uint32_t iterations = 0;
        uint32_t parallelism = 0;
        Data salt;
    };

    // A hash and what it was made with, read from or written as a PHC
    // string.
    class PhcString {
    public:
        PhcString(Parameters parameters, Data hash);

        [[nodiscard]] static std::optional<PhcString> parse(std::string_view text);
        [[nodiscard]] std::string to_string() const;
        [[nodiscard]] bool matches(std::string_view password) const;

    private:
        // How every PHC string this reads or writes starts: Argon2id, at
        // version 0x13, the only version OpenSSL's Argon2id makes.
        static constexpr std::string_view prefix = "$argon2id$v=19$m=";

        Parameters m_parameters;
        Data m_hash;

        [[nodiscard]] static bool _skip(std::string_view& text, std::string_view expected);
        [[nodiscard]] static bool _read_number(std::string_view& text, uint32_t& number);
        [[nodiscard]] static std::optional<Data> _read_base64(std::string_view text);
        [[nodiscard]] static std::string _base64(const Data& data);
    };

    uint32_t m_memory_cost_kib;
    uint32_t m_iterations;
    uint32_t m_parallelism;
    std::size_t m_salt_length;
    std::size_t m_hash_length;

    [[nodiscard]] static Data _derive(std::string_view password, const Parameters& parameters,
                                      std::size_t hash_length);
};

inline Argon2idOpenSsl::Argon2idOpenSsl(
    uint32_t memory_cost_kib,
    uint32_t iterations,
    uint32_t parallelism,
    std::size_t salt_length,
    std::size_t hash_length)
    : m_memory_cost_kib(memory_cost_kib)
    , m_iterations(iterations)
    , m_parallelism(parallelism)
    , m_salt_length(salt_length)
    , m_hash_length(hash_length) {}

inline std::string Argon2idOpenSsl::hash_password(std::string_view password) const {
    Parameters parameters{m_memory_cost_kib, m_iterations, m_parallelism, Data(m_salt_length)};

    if (RAND_bytes(OpenSslBytes::of(parameters.salt), static_cast<int>(parameters.salt.size()))
        != 1) {
        throw OpenSslError("Failed to make a salt for Argon2id");
    }

    auto hash = _derive(password, parameters, m_hash_length);

    return PhcString(std::move(parameters), std::move(hash)).to_string();
}

inline bool Argon2idOpenSsl::verify_password(std::string_view password,
                                             std::string_view hash) const {
    const auto encoded = PhcString::parse(hash);

    return encoded && encoded->matches(password);
}

inline Argon2idOpenSsl::Data Argon2idOpenSsl::_derive(std::string_view password,
                                                      const Parameters& parameters,
                                                      std::size_t hash_length) {
    const OpenSslObject<EVP_KDF, EVP_KDF_free> kdf(EVP_KDF_fetch(nullptr, "ARGON2ID", nullptr));

    if (!kdf) {
        throw OpenSslError("OpenSSL has no Argon2id");
    }

    const OpenSslObject<EVP_KDF_CTX, EVP_KDF_CTX_free> context(EVP_KDF_CTX_new(kdf.get()));

    if (!context) {
        throw OpenSslError("Failed to start Argon2id");
    }

    // OSSL_PARAM takes pointers to what is not const, though OpenSSL only
    // reads what a parameter given to it points to. The lanes are computed
    // one after another, on this thread; they change the hash, but how many
    // threads compute them does not.
    auto memory = parameters.memory_cost_kib;
    auto passes = parameters.iterations;
    auto lanes = parameters.parallelism;
    // NOLINTBEGIN(cppcoreguidelines-pro-type-const-cast)
    std::array arguments{
        OSSL_PARAM_construct_octet_string(OSSL_KDF_PARAM_PASSWORD,
                                          const_cast<char*>(password.data()), password.size()),
        OSSL_PARAM_construct_octet_string(OSSL_KDF_PARAM_SALT,
                                          const_cast<std::byte*>(parameters.salt.data()),
                                          parameters.salt.size()),
        OSSL_PARAM_construct_uint32(OSSL_KDF_PARAM_ITER, &passes),
        OSSL_PARAM_construct_uint32(OSSL_KDF_PARAM_ARGON2_MEMCOST, &memory),
        OSSL_PARAM_construct_uint32(OSSL_KDF_PARAM_ARGON2_LANES, &lanes),
        OSSL_PARAM_construct_end(),
    };
    // NOLINTEND(cppcoreguidelines-pro-type-const-cast)
    Data derived(hash_length);

    if (EVP_KDF_derive(context.get(), OpenSslBytes::of(derived), derived.size(), arguments.data())
        != 1) {
        throw OpenSslError("Argon2id failed");
    }

    return derived;
}

inline Argon2idOpenSsl::PhcString::PhcString(Parameters parameters, Data hash)
    : m_parameters(std::move(parameters)), m_hash(std::move(hash)) {}

inline std::optional<Argon2idOpenSsl::PhcString>
Argon2idOpenSsl::PhcString::parse(std::string_view text) {
    Parameters parameters{};

    if (!_skip(text, prefix) || !_read_number(text, parameters.memory_cost_kib)
        || !_skip(text, ",t=") || !_read_number(text, parameters.iterations)
        || !_skip(text, ",p=") || !_read_number(text, parameters.parallelism)
        || !_skip(text, "$")) {
        return std::nullopt;
    }

    const auto separator = text.find('$');

    if (std::string_view::npos == separator) {
        return std::nullopt;
    }

    auto salt = _read_base64(text.substr(0, separator));
    auto hash = _read_base64(text.substr(separator + 1));

    if (!salt || !hash) {
        return std::nullopt;
    }

    parameters.salt = std::move(*salt);
    return PhcString(std::move(parameters), std::move(*hash));
}

inline std::string Argon2idOpenSsl::PhcString::to_string() const {
    return std::string(prefix) + std::to_string(m_parameters.memory_cost_kib)
           + ",t=" + std::to_string(m_parameters.iterations)
           + ",p=" + std::to_string(m_parameters.parallelism)
           + "$" + _base64(m_parameters.salt) + "$" + _base64(m_hash);
}

// Whether password makes this hash, compared in constant time.
inline bool Argon2idOpenSsl::PhcString::matches(std::string_view password) const {
    Data computed;

    try {
        computed = _derive(password, m_parameters, m_hash.size());
    } catch (const OpenSslError&) {
        // Costs OpenSSL turns down, such as less memory than the lanes need.
        return false;
    }

    return 0 == CRYPTO_memcmp(computed.data(), m_hash.data(), computed.size());
}

// Moves past expected at the start of text, if it is there.
inline bool Argon2idOpenSsl::PhcString::_skip(std::string_view& text, std::string_view expected) {
    if (text.substr(0, expected.size()) != expected) {
        return false;
    }

    text.remove_prefix(expected.size());
    return true;
}

// Reads the decimal digits at the start of text, and moves past them. There
// must be at least one, and the number they make must fit in 32 bits.
inline bool Argon2idOpenSsl::PhcString::_read_number(std::string_view& text, uint32_t& number) {
    constexpr uint64_t base = 10;
    uint64_t value = 0;
    std::size_t digits = 0;

    for (const char character : text) {
        if (character < '0' || character > '9') {
            break;
        }

        value = (value * base) + static_cast<uint64_t>(character - '0');

        if (value > std::numeric_limits<uint32_t>::max()) {
            return false;
        }

        ++digits;
    }

    if (0 == digits) {
        return false;
    }

    number = static_cast<uint32_t>(value);
    text.remove_prefix(digits);
    return true;
}

// Base64 without padding, as PHC strings have it.
inline std::optional<Argon2idOpenSsl::Data>
Argon2idOpenSsl::PhcString::_read_base64(std::string_view text) {
    constexpr std::size_t group_size = 4;

    if (text.empty() || text.find('=') != std::string_view::npos
        || 1 == text.size() % group_size) {
        return std::nullopt;
    }

    std::string padded(text);

    padded.append((group_size - (padded.size() % group_size)) % group_size, '=');

    try {
        return Base64::decode(padded);
    } catch (const std::invalid_argument&) {
        return std::nullopt;
    }
}

inline std::string Argon2idOpenSsl::PhcString::_base64(const Data& data) {
    auto text = Base64::encode(data);

    text.erase(text.find_last_not_of('=') + 1);
    return text;
}

} // namespace crypto
