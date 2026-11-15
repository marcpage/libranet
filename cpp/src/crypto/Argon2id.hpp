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
class Argon2id : public IPasswordHasher {
public:
    Argon2id(
        uint32_t memory_cost_kib,
        uint32_t iterations,
        uint32_t parallelism,
        std::size_t salt_length,
        std::size_t hash_length
    );

    ~Argon2id() override = default;

    Argon2id(const Argon2id&) = delete;
    Argon2id& operator=(const Argon2id&) = delete;
    Argon2id(Argon2id&&) noexcept = default;
    Argon2id& operator=(Argon2id&&) noexcept = default;

    [[nodiscard]] std::string hash_password(std::string_view password) const override;
    [[nodiscard]] bool verify_password(std::string_view password, std::string_view hash) const override;

private:
    using Data = std::vector<std::byte>;

    // What a hash is made with, besides the password.
    struct Parameters {
        uint32_t memory_cost_kib;
        uint32_t iterations;
        uint32_t parallelism;
        Data salt;
    };

    // A hash and what it was made with, read from or written as a PHC
    // string.
    class PhcString {
    public:
        PhcString(Parameters parameters, Data hash);

        [[nodiscard]] static std::optional<PhcString> parse(std::string_view text);
        [[nodiscard]] std::string str() const;
        [[nodiscard]] bool matches(std::string_view password) const;

    private:
        // How every PHC string this reads or writes starts: Argon2id, at
        // version 0x13, the only version OpenSSL's Argon2id makes.
        static constexpr std::string_view prefix = "$argon2id$v=19$m=";

        Parameters m_parameters;
        Data m_hash;

        [[nodiscard]] static bool skip(std::string_view& text, std::string_view expected);
        [[nodiscard]] static bool read_number(std::string_view& text, uint32_t& number);
        [[nodiscard]] static std::optional<Data> read_base64(std::string_view text);
        [[nodiscard]] static std::string base64(const Data& data);
    };

    uint32_t memory_cost_kib_;
    uint32_t iterations_;
    uint32_t parallelism_;
    std::size_t salt_length_;
    std::size_t hash_length_;

    [[nodiscard]] static Data derive(std::string_view password, const Parameters& parameters,
                                     std::size_t hash_length);
};

inline Argon2id::Argon2id(
    uint32_t memory_cost_kib,
    uint32_t iterations,
    uint32_t parallelism,
    std::size_t salt_length,
    std::size_t hash_length
)
    : memory_cost_kib_(memory_cost_kib)
    , iterations_(iterations)
    , parallelism_(parallelism)
    , salt_length_(salt_length)
    , hash_length_(hash_length) {}

inline std::string Argon2id::hash_password(std::string_view password) const {
    Parameters parameters{memory_cost_kib_, iterations_, parallelism_, Data(salt_length_)};

    if (RAND_bytes(OpenSslBytes::of(parameters.salt), static_cast<int>(parameters.salt.size()))
        != 1) {
        throw OpenSslError("Failed to make a salt for Argon2id");
    }

    auto hash = derive(password, parameters, hash_length_);

    return PhcString(std::move(parameters), std::move(hash)).str();
}

inline bool Argon2id::verify_password(std::string_view password, std::string_view hash) const {
    const auto encoded = PhcString::parse(hash);

    return encoded && encoded->matches(password);
}

inline Argon2id::Data Argon2id::derive(std::string_view password, const Parameters& parameters,
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

inline Argon2id::PhcString::PhcString(Parameters parameters, Data hash)
    : m_parameters(std::move(parameters)), m_hash(std::move(hash)) {}

inline std::optional<Argon2id::PhcString> Argon2id::PhcString::parse(std::string_view text) {
    Parameters parameters{};

    if (!skip(text, prefix) || !read_number(text, parameters.memory_cost_kib)
        || !skip(text, ",t=") || !read_number(text, parameters.iterations)
        || !skip(text, ",p=") || !read_number(text, parameters.parallelism)
        || !skip(text, "$")) {
        return std::nullopt;
    }

    const auto separator = text.find('$');

    if (separator == std::string_view::npos) {
        return std::nullopt;
    }

    auto salt = read_base64(text.substr(0, separator));
    auto hash = read_base64(text.substr(separator + 1));

    if (!salt || !hash) {
        return std::nullopt;
    }

    parameters.salt = std::move(*salt);
    return PhcString(std::move(parameters), std::move(*hash));
}

inline std::string Argon2id::PhcString::str() const {
    return std::string(prefix) + std::to_string(m_parameters.memory_cost_kib)
         + ",t=" + std::to_string(m_parameters.iterations)
         + ",p=" + std::to_string(m_parameters.parallelism)
         + "$" + base64(m_parameters.salt) + "$" + base64(m_hash);
}

// Whether password makes this hash, compared in constant time.
inline bool Argon2id::PhcString::matches(std::string_view password) const {
    Data computed;

    try {
        computed = derive(password, m_parameters, m_hash.size());
    } catch (const OpenSslError&) {
        // Costs OpenSSL turns down, such as less memory than the lanes need.
        return false;
    }

    return CRYPTO_memcmp(computed.data(), m_hash.data(), computed.size()) == 0;
}

// Moves past expected at the start of text, if it is there.
inline bool Argon2id::PhcString::skip(std::string_view& text, std::string_view expected) {
    if (text.substr(0, expected.size()) != expected) {
        return false;
    }

    text.remove_prefix(expected.size());
    return true;
}

// Reads the decimal digits at the start of text, and moves past them. There
// must be at least one, and the number they make must fit in 32 bits.
inline bool Argon2id::PhcString::read_number(std::string_view& text, uint32_t& number) {
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

    if (digits == 0) {
        return false;
    }

    number = static_cast<uint32_t>(value);
    text.remove_prefix(digits);
    return true;
}

// Base64 without padding, as PHC strings have it.
inline std::optional<Argon2id::Data> Argon2id::PhcString::read_base64(std::string_view text) {
    constexpr std::size_t group_size = 4;

    if (text.empty() || text.find('=') != std::string_view::npos
        || text.size() % group_size == 1) {
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

inline std::string Argon2id::PhcString::base64(const Data& data) {
    auto text = Base64::encode(data);

    text.erase(text.find_last_not_of('=') + 1);
    return text;
}

} // namespace crypto
